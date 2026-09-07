"""The policy fold, as behaviour rather than as a declaration.

EM3D-PR115-DECISION-0001 landed the contract; this is the part that makes it decide runs.
EM3D-NETWORK-DECISION-0001 (Option B) fixes what the three declared levels mean.

The property under test throughout: **a lower scope may only narrow.** Before this, the declared
``network_level`` in a lockfile WAS the grant, and six recognised level names all resolved to the
engine's default bridge -- so a package asking for ``restricted`` received exactly what one asking
for ``unrestricted`` received. Each test below fails on that old behaviour.
"""
from __future__ import annotations

import pytest

from agentnode_sdk.sandbox.composition import (
    KNOWN_NETWORK_LEVELS,
    NetworkRequestError,
    compose,
    network_mode,
    normalise_level,
    package_request,
    user_policy,
)
from agentnode_sdk.sandbox.contract import (
    NetworkRules,
    SandboxPolicy,
    Scope,
    merge_policies,
)


def granted(entry_perms, config=None):
    return network_mode(compose({"permissions": entry_perms}, config))


# ---------------------------------------------------------------- the three levels differ

class TestTheLevelsAreObservablyDifferent:
    """The defect this fixes: every named level produced the same open network."""

    def test_none_gets_no_socket(self):
        assert granted({"network_level": "none"}) == ("none", ())

    def test_a_missing_level_is_none_not_everything(self):
        assert granted({}) == ("none", ())
        assert granted({"network_level": ""}) == ("none", ())
        assert granted({"network_level": None}) == ("none", ())

    def test_unrestricted_gets_the_open_default(self):
        assert granted({"network_level": "unrestricted"}) == ("default", ())

    def test_restricted_gets_a_proxied_egress_bound_to_its_domains(self):
        mode, domains = granted(
            {"network_level": "restricted", "allowed_domains": ["api.example.com"]})
        assert mode == "egress"
        assert domains == ("api.example.com",)

    def test_restricted_is_not_the_same_grant_as_unrestricted(self):
        """The whole point. On the old mapping both sides of this were ('default', ())."""
        restricted = granted(
            {"network_level": "restricted", "allowed_domains": ["api.example.com"]})
        unrestricted = granted({"network_level": "unrestricted"})
        assert restricted != unrestricted
        assert restricted[0] == "egress" and unrestricted[0] == "default"

    def test_the_domains_are_canonicalised_and_deduplicated(self):
        mode, domains = granted({"network_level": "restricted",
                                 "allowed_domains": ["API.Example.com", "api.example.com"]})
        assert mode == "egress"
        assert domains == ("api.example.com",)


# ---------------------------------------------------------------- a lower scope may only narrow

class TestAPackageCannotWiden:
    def test_a_package_asking_for_everything_cannot_escape_a_user_allowlist(self):
        """The G1 property: PACKAGE is the lowest scope, so its request can only tighten."""
        user = SandboxPolicy(network=NetworkRules(
            enabled=True, allowed_destinations=frozenset({"a.com"})))
        merged = merge_policies({
            Scope.USER: user,
            Scope.PACKAGE: package_request({"permissions": {"network_level": "unrestricted"}}),
        })
        assert network_mode(merged) == ("egress", ("a.com",))

    def test_a_package_cannot_add_a_destination_the_user_did_not_allow(self):
        user = SandboxPolicy(network=NetworkRules(
            enabled=True, allowed_destinations=frozenset({"a.com"})))
        pkg = package_request({"permissions": {"network_level": "restricted",
                                               "allowed_domains": ["a.com", "evil.com"]}})
        merged = merge_policies({Scope.USER: user, Scope.PACKAGE: pkg})
        assert network_mode(merged) == ("egress", ("a.com",))

    def test_a_user_who_denies_the_network_cannot_be_overridden(self):
        assert granted({"network_level": "unrestricted"},
                       {"permissions": {"network": "deny"}}) == ("none", ())
        assert granted({"network_level": "restricted", "allowed_domains": ["a.com"]},
                       {"permissions": {"network": "deny"}}) == ("none", ())

    def test_a_fold_that_narrows_to_nothing_is_no_network_not_open_network(self):
        user = SandboxPolicy(network=NetworkRules(
            enabled=True, allowed_destinations=frozenset({"a.com"})))
        pkg = package_request({"permissions": {"network_level": "restricted",
                                               "allowed_domains": ["b.com"]}})
        merged = merge_policies({Scope.USER: user, Scope.PACKAGE: pkg})
        assert network_mode(merged) == ("none", ())

    def test_the_universe_value_is_the_identity_and_not_an_empty_set(self):
        """If ``None`` were spelled as ``frozenset()``, the first case below would come out open."""
        wide = NetworkRules(enabled=True, allowed_destinations=None)
        narrow = NetworkRules(enabled=True, allowed_destinations=frozenset({"a.com"}))
        assert NetworkRules._narrowed_by(wide, narrow).allowed_destinations == frozenset({"a.com"})
        assert NetworkRules._narrowed_by(narrow, wide).allowed_destinations == frozenset({"a.com"})
        assert NetworkRules._narrowed_by(wide, wide).allowed_destinations is None
        assert wide.is_unrestricted and not narrow.is_unrestricted

    def test_disabling_beats_enabling_in_either_order(self):
        off = NetworkRules(enabled=False, allowed_destinations=None)
        on = NetworkRules(enabled=True, allowed_destinations=None)
        assert not NetworkRules._narrowed_by(on, off).enabled
        assert not NetworkRules._narrowed_by(off, on).enabled


# ---------------------------------------------------------------- fail-closed on bad input

class TestUnusableDeclarationsAreRefused:
    @pytest.mark.parametrize("level", ["full", "limited", "wat", "RESTRICTED-ish", "internal-ish"])
    def test_an_unknown_level_is_refused_not_guessed(self, level):
        with pytest.raises(NetworkRequestError):
            package_request({"permissions": {"network_level": level}})

    def test_full_and_limited_are_not_quietly_aliased_to_open(self):
        """They were recognised by the old mapping and granted everything."""
        for level in ("full", "limited"):
            assert level not in KNOWN_NETWORK_LEVELS
            with pytest.raises(NetworkRequestError):
                package_request({"permissions": {"network_level": level}})

    @pytest.mark.parametrize("level", ["internal", "external"])
    def test_the_cli_translation_spellings_still_resolve(self, level):
        """internal/external are what the CLI writes into a lockfile; they are not declarations."""
        assert normalise_level(level) in KNOWN_NETWORK_LEVELS

    def test_restricted_without_an_allowlist_is_refused_before_anything_runs(self):
        for perms in ({"network_level": "restricted"},
                      {"network_level": "restricted", "allowed_domains": []},
                      {"network_level": "restricted", "allowed_domains": None}):
            with pytest.raises(NetworkRequestError, match="allowed_domains"):
                package_request({"permissions": perms})

    @pytest.mark.parametrize("bad", ["127.0.0.1", "localhost", "169.254.169.254", "single", ""])
    def test_an_unusable_destination_is_refused_at_declaration_time(self, bad):
        with pytest.raises(NetworkRequestError):
            package_request({"permissions": {"network_level": "restricted",
                                             "allowed_domains": [bad]}})

    def test_a_non_string_level_is_refused(self):
        for bad in (1, True, [], {}):
            with pytest.raises(NetworkRequestError):
                package_request({"permissions": {"network_level": bad}})

    def test_a_non_list_allowlist_is_refused(self):
        with pytest.raises(NetworkRequestError):
            package_request({"permissions": {"network_level": "restricted",
                                             "allowed_domains": "api.example.com"}})

    def test_an_absent_entry_is_no_network(self):
        assert network_mode(compose(None)) == ("none", ())
        assert network_mode(compose({})) == ("none", ())


# ---------------------------------------------------------------- the fold itself is a boundary

class TestTheFoldCannotBeSubverted:
    def test_a_widening_subclass_is_rejected_rather_than_trusted(self):
        class Widening(SandboxPolicy):
            def _narrowed_by(self, lower):
                return SandboxPolicy(network=NetworkRules(enabled=True,
                                                          allowed_destinations=None))

        with pytest.raises(TypeError, match="not exactly SandboxPolicy"):
            merge_policies({Scope.USER: Widening(), Scope.PACKAGE: SandboxPolicy()})

    def test_a_scope_outside_the_vocabulary_cannot_insert_itself(self):
        with pytest.raises(TypeError, match="closed Scope vocabulary"):
            merge_policies({99: SandboxPolicy()})

    def test_a_widening_network_part_is_rejected(self):
        class WideningRules(NetworkRules):
            def _narrowed_by(self, other):
                return NetworkRules(enabled=True, allowed_destinations=None)

        with pytest.raises(TypeError, match="NetworkRules is required"):
            merge_policies({Scope.USER: SandboxPolicy(network=WideningRules())})

    def test_the_user_scope_is_present_so_package_is_never_alone(self):
        """A fold with only PACKAGE in it would be unconstrained by construction."""
        merged = compose({"permissions": {"network_level": "unrestricted"}})
        assert isinstance(merged, SandboxPolicy)
        assert user_policy({}).network.enabled is True


# ---------------------------------------------------------------- D5, exactly

class TestRetentionIsD5:
    def test_the_shipped_defaults_are_the_founder_decision(self):
        r = SandboxPolicy().retention
        assert r.workspace == "destroy_on_result_handover"
        assert r.diagnostics_hours == 24
        assert r.audit_metadata_days == 30

    def test_a_lower_scope_can_shorten_retention_but_not_lengthen_it(self):
        from agentnode_sdk.sandbox.contract import Retention
        high = SandboxPolicy(retention=Retention(diagnostics_hours=24, audit_metadata_days=30))
        low = SandboxPolicy(retention=Retention(diagnostics_hours=1, audit_metadata_days=7))
        assert merge_policies({Scope.USER: high, Scope.PACKAGE: low}).retention.diagnostics_hours == 1
        longer = SandboxPolicy(retention=Retention(diagnostics_hours=999, audit_metadata_days=999))
        merged = merge_policies({Scope.USER: high, Scope.PACKAGE: longer}).retention
        assert merged.diagnostics_hours == 24 and merged.audit_metadata_days == 30
