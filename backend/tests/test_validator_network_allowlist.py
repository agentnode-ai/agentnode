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

    @pytest.mark.parametrize("level", ["none", "unrestricted"])
    def test_an_EMPTY_allowlist_at_those_levels_is_accepted(self, level):
        """Deliberate, and worth stating: the rule is about a NON-EMPTY allowlist.

        `allowed_domains: []` is what the manifest schema itself carries as the default (see the
        default permissions block in validator.py), so rejecting it would fail every manifest that
        simply left the field at its default. An empty list declares nothing, so there is nothing
        for the author to have expected to apply.
        """
        assert check(level, [])[0] == []
        assert check(level, ())[0] == []


class TestTheTwoCanonicalisersDoNotDrift:
    """The registry and the sandbox each have their own canonicaliser. That is two rule sets.

    EM3D-REGISTRY-FINAL-0001 was right to call the earlier version of this class a false claim: it
    was named after registry/SDK agreement and only ever compared the registry helper with the
    backend function that helper already calls, so it could not have detected SDK drift at all.

    `shared/allowed-domains-corpus.json` is the mechanism instead. Both implementations are loaded
    here -- by file path, since neither is installed in the other's environment, and both are
    stdlib-only -- and each must accept every listed host with the same canonical form and reject
    every listed one.

    What that establishes, exactly: a change to either side that alters its treatment of a host IN
    THE CORPUS fails this test. It is a finite corpus of 30 inputs, so it bounds the disagreement
    rather than excluding it -- a host outside the corpus could still be handled differently by the
    two. Removing the duplication would need a shared package both trees depend on, which is out of
    scope here and recorded as a known limitation rather than presented as solved.
    """

    @staticmethod
    def _load(rel_path, name):
        import importlib.util
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location(name, root / rel_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _corpus():
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        return json.loads(
            (root / "shared" / "allowed-domains-corpus.json").read_text(
                encoding="utf-8"
            )
        )

    @property
    def implementations(self):
        return {
            "backend": self._load("backend/app/mcp/mcp_policy.py", "_corpus_backend"),
            "sdk": self._load(
                "sdk/agentnode_sdk/sandbox/domain_policy.py", "_corpus_sdk"
            ),
        }

    def test_the_corpus_is_not_empty(self):
        c = self._corpus()
        assert len(c["accepted"]) >= 5 and len(c["rejected"]) >= 10

    def test_both_accept_every_accepted_host_with_the_same_canonical_form(self):
        implementations = self.implementations
        corpus = self._corpus()
        for name, module in implementations.items():
            for raw_host, canonical in corpus["accepted"].items():
                got = module.canonicalize_allowed_domains([raw_host])
                assert got == (canonical,), (
                    f"{name} canonicalised {raw_host!r} to {got!r}, corpus says {canonical!r}"
                )

    def test_both_reject_every_rejected_host(self):
        implementations = self.implementations
        corpus = self._corpus()
        for name, module in implementations.items():
            error_type = module.DomainPolicyError
            for raw_host in corpus["rejected"]:
                with pytest.raises(error_type):
                    module.canonicalize_allowed_domains([raw_host])

    def test_the_validator_itself_refuses_every_rejected_host(self):
        """The corpus binds the canonicalisers; this binds the validator to the corpus too."""
        for raw_host in self._corpus()["rejected"]:
            assert check("restricted", [raw_host])[0], (
                f"the validator accepted {raw_host!r}"
            )

    def test_the_validator_accepts_every_accepted_host(self):
        for raw_host in self._corpus()["accepted"]:
            assert check("restricted", [raw_host])[0] == [], (
                f"the validator rejected {raw_host!r}, which the corpus lists as usable"
            )
