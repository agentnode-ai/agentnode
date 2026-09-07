"""Registry-side half of EM3D-NETWORK-DECISION-0001 (Option B).

The SDK refuses at run time to start a `restricted` package whose `allowed_domains` is missing or
unusable — there is no restriction to enforce, so there is nothing to run. That refusal is correct
and it arrives late: the publisher has already shipped, and the person installing finds out when it
will not start. These rules move the same judgement to submission time.

The hosts are judged by the canonicaliser the MCP policy already uses, not by a second rule set
written here, so the registry and the runtime cannot drift apart about what a usable host is.
"""

from __future__ import annotations

import pytest

from app.packages.validator import _validate_network_allowlist


def check(level, domains=None):
    net = {"level": level}
    if domains is not None:
        net["allowed_domains"] = domains
    errors, warnings = [], []
    _validate_network_allowlist(net, errors, warnings)
    return errors, warnings


class TestRestrictedMustDeclareWhatItReaches:
    @pytest.mark.parametrize("domains", [None, [], (), "api.example.com", 0, {}])
    def test_restricted_without_a_usable_list_is_an_error(self, domains):
        errors, _ = check("restricted", domains)
        assert errors, f"{domains!r} should not be accepted as an allowlist"
        assert "restricted" in errors[0]

    def test_a_good_allowlist_passes(self):
        errors, warnings = check("restricted", ["api.example.com", "cdn.example.org"])
        assert errors == []

    def test_case_and_trailing_dot_are_tolerated(self):
        errors, _ = check("restricted", ["API.Example.com."])
        assert errors == []

    @pytest.mark.parametrize(
        "bad",
        [
            "127.0.0.1",  # an IP literal is not a hostname
            "169.254.169.254",  # cloud metadata
            "localhost",
            "single",  # single label
            "https://api.example.com",  # scheme
            "api.example.com/path",  # path
            "api.example.com:443",  # port
            "user@api.example.com",  # userinfo
            "",
            "  ",
            "exämple.com",  # non-ASCII, punycode required
        ],
    )
    def test_an_unusable_host_is_rejected_with_a_reason(self, bad):
        errors, _ = check("restricted", [bad])
        assert errors, f"{bad!r} should be rejected"
        assert "not usable" in errors[0]

    def test_one_bad_host_rejects_the_whole_list(self):
        errors, _ = check("restricted", ["api.example.com", "127.0.0.1"])
        assert errors


class TestTheAllowlistIsNotSilentlyIgnored:
    @pytest.mark.parametrize("level", ["none", "unrestricted"])
    def test_declaring_hosts_at_a_level_that_ignores_them_is_an_error(self, level):
        """The author plainly expected these to apply. Ignoring them quietly is the worse answer."""
        errors, _ = check(level, ["api.example.com"])
        assert errors
        assert "only enforced for 'restricted'" in errors[0]

    @pytest.mark.parametrize("level", ["none", "unrestricted"])
    def test_no_allowlist_at_those_levels_is_fine(self, level):
        assert check(level, None)[0] == []
        assert check(level, [])[0] == []


class TestItAgreesWithTheRuntime:
    def test_the_registry_and_the_sdk_reject_the_same_hosts(self):
        """Both sides consult a canonicaliser with the same rules; this pins that they agree.

        The SDK's copy lives in agentnode_sdk.sandbox.domain_policy and is not importable here, so
        the agreement is asserted against the backend's own canonicaliser — the one the validator
        actually calls — for the cases that matter most.
        """
        from app.mcp.mcp_policy import DomainPolicyError, canonicalize_allowed_domains

        for bad in ("127.0.0.1", "localhost", "single", "https://x.example.com"):
            with pytest.raises(DomainPolicyError):
                canonicalize_allowed_domains([bad])
            assert check("restricted", [bad])[0], f"validator accepted {bad!r}"

        assert canonicalize_allowed_domains(["B.example.com", "a.example.com"]) == (
            "a.example.com",
            "b.example.com",
        )
