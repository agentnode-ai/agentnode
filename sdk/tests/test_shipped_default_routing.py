"""The SHIPPED host-trust default, exercised without the legacy-compat mask.

There is no suite-wide policy mask: R3 Route A (EM2-AC-REMEDIATION-DECISION-0001)
removed it, so an unannotated test observes the shipped default. Legacy tests that
need the older value declare it in an explicit, locally scoped fixture.

This file asserts the shipped value itself, the pure decision table, and where code
is routed under it.

Decision of record: EM-1 / EXEC-MODEL-SCOPE-0001, option 1B — the shipped default
is ``curated_only``: trusted THIRD-PARTY tool packs and MCP servers run in the
container or are refused fail-closed; only curated (AgentNode-owned) code is
host-eligible.
"""
from __future__ import annotations

import pytest

import agentnode_sdk.runtimes.python_runner as pr
from agentnode_sdk.config import (
    DEFAULTS,
    host_trust_policy,
    read_host_trust_policy_snapshot,
)
from agentnode_sdk.sandbox.policy import (
    host_allowed_tiers,
    requires_sandbox_for_policy,
)


@pytest.fixture(autouse=True)
def _clean_config_dir(monkeypatch, tmp_path):
    """A fresh config directory, so these tests see exactly what a new install
    would."""
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))
    yield


class TestShippedDefaultValue:
    def test_defaults_dict_pins_curated_only(self):
        assert DEFAULTS["sandbox"]["host_trust_policy"] == "curated_only"

    def test_live_reader_returns_curated_only(self):
        assert host_trust_policy() == "curated_only"

    def test_snapshot_reader_returns_curated_only(self):
        assert read_host_trust_policy_snapshot() == "curated_only"

    def test_missing_key_fallback_is_not_more_permissive(self, monkeypatch):
        """An unreadable or key-less config must not be more permissive than a
        fresh install."""
        monkeypatch.setattr("agentnode_sdk.config.load_config", lambda: {})
        assert read_host_trust_policy_snapshot() == "curated_only"


class TestShippedDefaultDecisionTable:
    """The pure table, at the shipped default. No config, no I/O."""

    def test_only_curated_is_host_eligible(self):
        assert host_allowed_tiers("curated_only") == frozenset({"curated"})

    @pytest.mark.parametrize("tier,sandboxed", [
        ("curated", False),
        ("trusted", True),
        ("verified", True),
        ("unverified", True),
        (None, True),
        ("nonsense", True),
    ])
    def test_requires_sandbox(self, tier, sandboxed):
        assert requires_sandbox_for_policy(tier, "curated_only") is sandboxed


class TestShippedDefaultToolpackRouting:
    """End-to-end through run_python, with the routing observable via mode_used."""

    def _route(self, monkeypatch, trust, mode="auto"):
        from tests.hostpolicy import decision
        monkeypatch.setattr(pr, "_run_container", lambda *a, **k: ("C", None, False))
        monkeypatch.setattr(pr, "_run_subprocess", lambda *a, **k: ("S", None, False))
        monkeypatch.setattr(pr, "_run_direct", lambda *a, **k: "D")
        monkeypatch.setattr(pr, "_get_trust_level", lambda *a, **k: trust)
        return pr.run_python(
            "p", "do", entry={"trust_level": trust}, mode=mode,
            _host_policy_decision=decision(trust, "curated_only"),
        ).mode_used

    def test_curated_runs_on_the_host(self, monkeypatch):
        assert self._route(monkeypatch, "curated") == "subprocess"

    @pytest.mark.parametrize("tier", ["trusted", "verified", "unverified"])
    def test_everything_else_is_sandboxed(self, monkeypatch, tier):
        assert self._route(monkeypatch, tier) == "sandbox"

    def test_direct_mode_cannot_put_trusted_back_on_the_host(self, monkeypatch):
        """An explicit mode='direct' must not bypass the shipped default — the
        sandbox requirement is checked before mode resolution."""
        assert self._route(monkeypatch, "trusted", mode="direct") == "sandbox"
