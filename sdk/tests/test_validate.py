"""Tests for agentnode validate — package validation with risk preview."""
import json
import textwrap
from pathlib import Path
from unittest import mock

import pytest
import yaml

from agentnode_sdk.cli.validate import (
    ValidateResult,
    _manifest_to_risk_entry,
    _check_risk_preview,
    _build_risk_hints,
    validate_package_dir,
)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    lock_file = tmp_path / "agentnode.lock"
    cfg_file.write_text(json.dumps({"version": "0.1"}))
    lock_file.write_text(json.dumps({"version": "0.1", "packages": {}}))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lock_file))
    monkeypatch.setenv("AGENTNODE_CONFIG_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_manifest(tmp_path: Path, manifest: dict) -> Path:
    """Write a manifest to tmp_path/agentnode.yaml and return the dir."""
    (tmp_path / "agentnode.yaml").write_text(
        yaml.dump(manifest), encoding="utf-8",
    )
    return tmp_path


def _minimal_manifest(**overrides) -> dict:
    m = {
        "package_id": "test-pack",
        "version": "1.0.0",
        "package_type": "toolpack",
        "summary": "A test package for validation testing purposes.",
        "capabilities": {
            "tools": [{"name": "run", "capability_id": "test_cap"}],
        },
    }
    m.update(overrides)
    return m


# ---------------------------------------------------------------------------
# _manifest_to_risk_entry
# ---------------------------------------------------------------------------

class TestManifestToRiskEntry:
    def test_network_unrestricted_maps_to_external(self):
        entry = _manifest_to_risk_entry({
            "permissions": {"network": {"level": "unrestricted"}},
        })
        assert entry["permissions"]["network_level"] == "external"

    def test_network_restricted_maps_to_internal(self):
        entry = _manifest_to_risk_entry({
            "permissions": {"network": {"level": "restricted"}},
        })
        assert entry["permissions"]["network_level"] == "internal"

    def test_network_none_stays_none(self):
        entry = _manifest_to_risk_entry({
            "permissions": {"network": {"level": "none"}},
        })
        assert entry["permissions"]["network_level"] == "none"

    def test_no_permissions_defaults_to_none(self):
        entry = _manifest_to_risk_entry({})
        assert entry["permissions"]["network_level"] == "none"
        assert entry["permissions"]["filesystem_level"] == "none"
        assert entry["permissions"]["code_execution_level"] == "none"

    def test_extracts_capability_ids(self):
        entry = _manifest_to_risk_entry({
            "capabilities": {
                "tools": [
                    {"name": "send", "capability_id": "email_sending"},
                    {"name": "read", "capability_id": "email_reading"},
                ],
            },
        })
        assert entry["capability_ids"] == ["email_sending", "email_reading"]

    def test_trust_level_is_preview(self):
        entry = _manifest_to_risk_entry({})
        assert entry["trust_level"] == "preview"

    def test_connector_none_when_missing(self):
        entry = _manifest_to_risk_entry({})
        assert entry["connector"] is None

    def test_connector_mapped_from_manifest(self):
        entry = _manifest_to_risk_entry({
            "connector": {"provider": "slack", "auth_type": "oauth2"},
        })
        assert entry["connector"] == {"auth_type": "oauth2"}

    def test_connector_none_without_auth_type(self):
        entry = _manifest_to_risk_entry({
            "connector": {"provider": "slack"},
        })
        assert entry["connector"] is None

    def test_connector_declared_flag(self):
        entry = _manifest_to_risk_entry({
            "connector": {"provider": "slack", "auth_type": "oauth2"},
        })
        assert entry["_connector_declared"] is True

    def test_connector_declared_false_when_missing(self):
        entry = _manifest_to_risk_entry({})
        assert entry["_connector_declared"] is False

    def test_connector_scopes_extracted(self):
        entry = _manifest_to_risk_entry({
            "connector": {"auth_type": "oauth2", "scopes": ["chat:write", "channels:read"]},
        })
        assert entry["_connector_scopes"] == ["chat:write", "channels:read"]

    def test_connector_scopes_empty_when_missing(self):
        entry = _manifest_to_risk_entry({})
        assert entry["_connector_scopes"] == []

    def test_permissions_declared_true(self):
        entry = _manifest_to_risk_entry({
            "permissions": {"network": {"level": "none"}},
        })
        assert entry["_manifest_permissions_declared"] is True

    def test_permissions_declared_false(self):
        entry = _manifest_to_risk_entry({})
        assert entry["_manifest_permissions_declared"] is False


# ---------------------------------------------------------------------------
# Risk Preview
# ---------------------------------------------------------------------------

class TestRiskPreview:
    def test_low_risk_pdf_style(self):
        manifest = _minimal_manifest(permissions={
            "network": {"level": "none"},
            "filesystem": {"level": "temp"},
        })
        result = ValidateResult()
        _check_risk_preview(manifest, result)
        assert result.risk_level == "low"
        assert result.risk_score <= 20

    def test_medium_risk_network(self):
        manifest = _minimal_manifest(permissions={
            "network": {"level": "unrestricted"},
            "filesystem": {"level": "none"},
        })
        result = ValidateResult()
        _check_risk_preview(manifest, result)
        assert result.risk_level == "medium"
        assert result.risk_score == 25

    def test_flags_detected(self):
        manifest = _minimal_manifest(
            permissions={"network": {"level": "unrestricted"}},
            capabilities={
                "tools": [{"name": "send", "capability_id": "email_sending"}],
            },
        )
        result = ValidateResult()
        _check_risk_preview(manifest, result)
        assert "external_write_capable" in result.risk_flags

    def test_connector_adds_credential_signal(self):
        manifest = _minimal_manifest(
            connector={"auth_type": "oauth2"},
            permissions={"network": {"level": "unrestricted"}},
        )
        result = ValidateResult()
        _check_risk_preview(manifest, result)
        assert result.risk_score == 40  # 25 (network) + 15 (credentials)
        assert any("credentials" in s for s in result.risk_signals)

    def test_connector_triggers_external_write_flag(self):
        manifest = _minimal_manifest(
            connector={"provider": "slack", "auth_type": "oauth2"},
            permissions={"network": {"level": "unrestricted"}},
        )
        result = ValidateResult()
        _check_risk_preview(manifest, result)
        assert "external_write_capable" in result.risk_flags

    def test_connector_without_auth_no_credential_signal(self):
        manifest = _minimal_manifest(
            connector={"provider": "slack"},
            permissions={"network": {"level": "unrestricted"}},
        )
        result = ValidateResult()
        _check_risk_preview(manifest, result)
        assert result.risk_score == 25
        assert not any("credentials" in s for s in result.risk_signals)

    def test_preview_trust_gives_zero_points(self):
        manifest = _minimal_manifest(permissions={
            "network": {"level": "none"},
            "filesystem": {"level": "none"},
        })
        result = ValidateResult()
        _check_risk_preview(manifest, result)
        assert result.risk_score == 0
        assert not any("Trust level" in s for s in result.risk_signals)


# ---------------------------------------------------------------------------
# Hints
# ---------------------------------------------------------------------------

class TestRiskHints:
    def test_external_write_hint(self):
        manifest = _minimal_manifest(
            permissions={"network": {"level": "unrestricted"}},
            capabilities={
                "tools": [{"name": "send", "capability_id": "email_sending"}],
            },
        )
        result = ValidateResult()
        _check_risk_preview(manifest, result)
        assert any("external_write_capable" in h for h in result.risk_hints)

    def test_no_permissions_hint(self):
        manifest = _minimal_manifest()
        result = ValidateResult()
        _check_risk_preview(manifest, result)
        assert any("No permissions declared" in h for h in result.risk_hints)

    def test_trust_after_publish_hint(self):
        manifest = _minimal_manifest()
        result = ValidateResult()
        _check_risk_preview(manifest, result)
        assert any("after publish" in h for h in result.risk_hints)

    def test_write_scopes_hint(self):
        manifest = _minimal_manifest(
            connector={"auth_type": "oauth2", "scopes": ["chat:write"]},
            permissions={"network": {"level": "unrestricted"}},
        )
        result = ValidateResult()
        _check_risk_preview(manifest, result)
        assert any("write scopes" in h for h in result.risk_hints)

    def test_no_connector_with_network_hint(self):
        manifest = _minimal_manifest(
            permissions={"network": {"level": "unrestricted"}},
        )
        result = ValidateResult()
        _check_risk_preview(manifest, result)
        assert any("connector metadata" in h for h in result.risk_hints)

    def test_connector_suppresses_no_connector_hint(self):
        manifest = _minimal_manifest(
            connector={"provider": "slack", "auth_type": "oauth2"},
            permissions={"network": {"level": "unrestricted"}},
        )
        result = ValidateResult()
        _check_risk_preview(manifest, result)
        assert not any("connector metadata" in h for h in result.risk_hints)

    def test_high_risk_hint(self):
        manifest = _minimal_manifest(permissions={
            "network": {"level": "unrestricted"},
            "filesystem": {"level": "write"},
            "code_execution": {"level": "subprocess"},
        })
        result = ValidateResult()
        _check_risk_preview(manifest, result)
        assert result.risk_level == "high"
        assert any("High usage risk" in h for h in result.risk_hints)


# ---------------------------------------------------------------------------
# Full validate flow
# ---------------------------------------------------------------------------

class TestValidateRiskIntegration:
    def test_validate_result_has_risk_fields(self, tmp_path):
        pkg_dir = _write_manifest(tmp_path, _minimal_manifest(
            permissions={"network": {"level": "unrestricted"}},
        ))
        result = validate_package_dir(pkg_dir)
        assert result.risk_level in ("low", "medium", "high")
        assert isinstance(result.risk_score, int)
        assert isinstance(result.risk_signals, list)
        assert isinstance(result.risk_flags, list)
        assert isinstance(result.risk_hints, list)

    def test_validate_broken_manifest_no_risk_crash(self, tmp_path):
        (tmp_path / "agentnode.yaml").write_text("not: valid: yaml: {", encoding="utf-8")
        result = validate_package_dir(tmp_path)
        assert result.has_errors
        assert result.risk_level == ""

    def test_validate_connector_fields(self, tmp_path):
        pkg_dir = _write_manifest(tmp_path, _minimal_manifest(
            connector={"provider": "slack", "auth_type": "oauth2", "scopes": ["chat:write"]},
            permissions={"network": {"level": "unrestricted"}},
        ))
        result = validate_package_dir(pkg_dir)
        assert result.connector_provider == "slack"
        assert result.connector_auth_type == "oauth2"
        assert result.connector_scopes == ["chat:write"]

    def test_validate_no_connector_empty_fields(self, tmp_path):
        pkg_dir = _write_manifest(tmp_path, _minimal_manifest(
            permissions={"network": {"level": "unrestricted"}},
        ))
        result = validate_package_dir(pkg_dir)
        assert result.connector_provider == ""
        assert result.connector_auth_type == ""
        assert result.connector_scopes == []

    def test_cmd_validate_shows_connector(self, tmp_path, capsys):
        _write_manifest(tmp_path, _minimal_manifest(
            connector={"provider": "slack", "auth_type": "oauth2", "scopes": ["chat:write"]},
            permissions={"network": {"level": "unrestricted"}},
        ))
        from agentnode_sdk.cli.commands import cmd_validate
        cmd_validate(str(tmp_path))
        out = capsys.readouterr().out
        assert "Connector" in out
        assert "slack" in out
        assert "oauth2" in out
        assert "chat:write" in out

    def test_cmd_validate_shows_risk_section(self, tmp_path, capsys):
        _write_manifest(tmp_path, _minimal_manifest(
            permissions={"network": {"level": "unrestricted"}},
        ))
        from agentnode_sdk.cli.commands import cmd_validate
        cmd_validate(str(tmp_path))
        out = capsys.readouterr().out
        assert "Usage Risk Preview" in out
        assert "Risk level" in out
        assert "Risk score" in out
        assert "Hints" in out
