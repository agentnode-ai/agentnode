"""Tests for risk_profile — usage risk scoring."""
import json

import pytest

from agentnode_sdk.risk_profile import RiskProfile, compute_risk_profile


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    lock_file = tmp_path / "agentnode.lock"
    audit_file = tmp_path / "audit.jsonl"
    cfg_file.write_text(json.dumps({"version": "0.1"}))
    lock_file.write_text(json.dumps({"version": "0.1", "packages": {}}))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lock_file))
    monkeypatch.setenv("AGENTNODE_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _entry(*, network="none", filesystem="none", code_execution="none",
           trust="verified", connector=None, **extra):
    e = {
        "permissions": {
            "network_level": network,
            "filesystem_level": filesystem,
            "code_execution_level": code_execution,
        },
        "trust_level": trust,
    }
    if connector:
        e["connector"] = connector
    e.update(extra)
    return e


class TestStaticSignals:
    def test_low_risk_no_permissions(self):
        profile = compute_risk_profile("safe-pack", _entry(trust="trusted"))
        assert profile.risk_level == "low"
        assert profile.risk_score <= 20
        assert profile.signals == []

    def test_high_risk_network_and_code(self):
        profile = compute_risk_profile("risky-pack", _entry(
            network="external", code_execution="sandboxed", trust="unverified",
        ))
        assert profile.risk_level == "high"
        assert profile.risk_score >= 46

    def test_medium_risk_credentials(self):
        profile = compute_risk_profile("api-pack", _entry(
            network="external", trust="verified",
            connector={"auth_type": "oauth", "provider": "google"},
        ))
        assert profile.risk_level == "medium"
        assert 21 <= profile.risk_score <= 45

    def test_trust_unverified_adds_risk(self):
        verified = compute_risk_profile("a", _entry(trust="verified"))
        unverified = compute_risk_profile("b", _entry(trust="unverified"))
        assert unverified.risk_score > verified.risk_score

    def test_filesystem_read_vs_write(self):
        read = compute_risk_profile("a", _entry(filesystem="read", trust="trusted"))
        write = compute_risk_profile("b", _entry(filesystem="write", trust="trusted"))
        assert read.risk_score == 5
        assert write.risk_score == 20

    def test_trusted_curated_zero_trust_points(self):
        for level in ("trusted", "curated"):
            profile = compute_risk_profile("a", _entry(trust=level))
            assert not any("Trust level" in s for s in profile.signals)


class TestRuntimeSignals:
    def test_deny_rate_signal(self, isolated_env):
        audit_path = isolated_env / "audit.jsonl"
        lines = []
        for i in range(10):
            action = "deny" if i < 3 else "allow"
            lines.append(json.dumps({
                "ts": "2026-05-01T00:00:00", "slug": "test-pack",
                "action": action, "event": "run_tool",
            }))
        audit_path.write_text("\n".join(lines))

        profile = compute_risk_profile("test-pack", _entry(trust="trusted"))
        assert any("deny" in s.lower() for s in profile.signals)
        assert profile.risk_score >= 10

    def test_no_audit_data_still_works(self):
        profile = compute_risk_profile("new-pack", _entry(trust="trusted"))
        assert profile.risk_level == "low"
        assert isinstance(profile.signals, list)


class TestBackendHint:
    def test_no_backend_hint(self):
        profile = compute_risk_profile("a", _entry())
        assert profile.backend_hint is None

    def test_backend_hint_numeric(self):
        profile = compute_risk_profile("a", _entry(risk_score=72))
        assert profile.backend_hint == {"score": 72}

    def test_backend_hint_dict(self):
        profile = compute_risk_profile("a", _entry(
            risk_profile={"score": 60, "level": "high", "signals": ["flagged"]},
        ))
        assert profile.backend_hint["score"] == 60
        assert profile.backend_hint["level"] == "high"

    def test_backend_hint_not_in_local_score(self):
        without = compute_risk_profile("a", _entry(trust="trusted"))
        with_hint = compute_risk_profile("a", _entry(trust="trusted", risk_score=90))
        assert without.risk_score == with_hint.risk_score


class TestEdgeCases:
    def test_signals_are_human_readable(self):
        profile = compute_risk_profile("p", _entry(
            network="external", filesystem="write", trust="unverified",
        ))
        for sig in profile.signals:
            assert isinstance(sig, str)
            assert len(sig) > 5

    def test_risk_independent_of_verification(self):
        entry = _entry(network="external", filesystem="write", trust="unverified")
        entry["verification_tier"] = "gold"
        entry["verification_score"] = 95
        profile = compute_risk_profile("p", entry)
        assert profile.risk_level == "high"

    def test_score_capped_at_100(self):
        profile = compute_risk_profile("p", _entry(
            network="external", filesystem="write", code_execution="full",
            trust="unverified",
            connector={"auth_type": "api_key"},
        ))
        assert profile.risk_score <= 100

    def test_no_permissions_entry(self):
        profile = compute_risk_profile("p", {"trust_level": "verified"})
        assert profile.risk_level in ("low", "medium", "high")
