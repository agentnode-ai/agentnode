"""Tests for agentnode_sdk CLI."""
import base64
import hashlib as _hashlib
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agentnode_sdk.cli.main import main
from agentnode_sdk.cli.output import set_color


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Isolate config and lockfile for every test."""
    cfg_file = tmp_path / "config.json"
    lock_file = tmp_path / "agentnode.lock"
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lock_file))
    set_color(False)
    return tmp_path


@pytest.fixture
def saved_config(isolated_env):
    """Create a default config file."""
    from agentnode_sdk.config import default_config, save_config

    cfg = default_config()
    save_config(cfg)
    return cfg


@pytest.fixture
def lockfile_with_packages(isolated_env):
    """Create a lockfile with sample packages."""
    lock_path = isolated_env / "agentnode.lock"
    data = {
        "lockfile_version": "0.1",
        "updated_at": "2026-03-27T00:00:00+00:00",
        "packages": {
            "pdf-reader-pack": {
                "version": "1.2.0",
                "package_type": "toolpack",
                "runtime": "python",
                "entrypoint": "pdf_reader.tool",
                "capability_ids": ["pdf_extraction"],
                "tools": [],
                "artifact_hash": "sha256:abc123",
                "installed_at": "2026-03-27T00:00:00+00:00",
                "source": "sdk",
                "trust_level": "verified",
                "permissions": {
                    "network_level": "none",
                    "filesystem_level": "read",
                    "code_execution_level": "none",
                    "data_access_level": "read",
                    "user_approval_level": "none",
                },
            },
            "csv-analyzer-pack": {
                "version": "0.5.0",
                "package_type": "toolpack",
                "runtime": "python",
                "entrypoint": "csv_analyzer.tool",
                "capability_ids": ["csv_analysis"],
                "tools": [],
                "artifact_hash": "sha256:def456",
                "installed_at": "2026-03-27T00:00:00+00:00",
                "source": "sdk",
                "trust_level": "trusted",
                "permissions": None,
            },
        },
    }
    lock_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


# --- Version ---


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "agentnode" in out
    import agentnode_sdk
    assert agentnode_sdk.__version__ in out


# --- Dashboard ---


def test_dashboard_no_config_triggers_setup(capsys):
    """When no config exists, dashboard should try to run wizard."""
    with patch("agentnode_sdk.cli.setup_wizard.run_wizard", return_value=0) as mock:
        code = main([])
    assert code == 0
    mock.assert_called_once()


def test_dashboard_with_config(capsys, saved_config):
    code = main([])
    assert code == 0
    out = capsys.readouterr().out
    assert "AgentNode Settings" in out
    assert "automatic" in out
    assert "verified" in out


def test_dashboard_shows_capabilities_count(capsys, saved_config, lockfile_with_packages):
    code = main([])
    assert code == 0
    out = capsys.readouterr().out
    assert "2" in out


# --- Config ---


def test_config_show(capsys, saved_config):
    code = main(["config"])
    assert code == 0
    out = capsys.readouterr().out
    assert "auto_upgrade_policy" in out
    assert "safe" in out


def test_config_get(capsys, saved_config):
    code = main(["config", "get", "auto_upgrade_policy"])
    assert code == 0
    out = capsys.readouterr().out.strip()
    assert out == "safe"


def test_config_get_nested(capsys, saved_config):
    code = main(["config", "get", "permissions.network"])
    assert code == 0
    out = capsys.readouterr().out.strip()
    assert out == "prompt"


def test_config_get_unknown_key(capsys, saved_config):
    code = main(["config", "get", "nonexistent"])
    assert code == 1


def test_config_set(capsys, saved_config):
    code = main(["config", "set", "auto_upgrade_policy", "off"])
    assert code == 0
    capsys.readouterr()  # consume set output

    code = main(["config", "get", "auto_upgrade_policy"])
    out = capsys.readouterr().out.strip()
    assert out == "off"


def test_config_set_nested(capsys, saved_config):
    code = main(["config", "set", "permissions.network", "allow"])
    assert code == 0
    capsys.readouterr()  # consume set output

    code = main(["config", "get", "permissions.network"])
    out = capsys.readouterr().out.strip()
    assert out == "allow"


def test_config_set_invalid_value(capsys, saved_config):
    code = main(["config", "set", "auto_upgrade_policy", "invalid"])
    assert code == 1


def test_config_set_unknown_key(capsys, saved_config):
    code = main(["config", "set", "bad_key", "value"])
    assert code == 1


def test_config_show_risk_policies(capsys, saved_config):
    code = main(["config"])
    assert code == 0
    out = capsys.readouterr().out
    assert "risk_policies.external_write_capable" in out
    assert "log" in out


def test_config_set_risk_policy(capsys, saved_config):
    code = main(["config", "set", "risk_policies.external_write_capable", "prompt"])
    assert code == 0
    capsys.readouterr()

    code = main(["config", "get", "risk_policies.external_write_capable"])
    out = capsys.readouterr().out.strip()
    assert out == "prompt"


def test_config_list_shows_risk_policies(capsys, saved_config):
    code = main(["config", "list"])
    assert code == 0
    out = capsys.readouterr().out
    assert "risk_policies.external_write_capable" in out
    assert "allow, log, prompt, deny" in out


# --- Capabilities ---


def test_capabilities_empty(capsys, saved_config):
    code = main(["capabilities"])
    assert code == 0
    out = capsys.readouterr().out
    assert "No capabilities installed" in out


def test_capabilities_list(capsys, saved_config, lockfile_with_packages):
    code = main(["capabilities"])
    assert code == 0
    out = capsys.readouterr().out
    assert "pdf-reader-pack" in out
    assert "csv-analyzer-pack" in out
    assert "2 installed" in out


def test_capabilities_show(capsys, saved_config, lockfile_with_packages):
    code = main(["capabilities", "show", "pdf-reader-pack"])
    assert code == 0
    out = capsys.readouterr().out
    assert "pdf-reader-pack" in out
    assert "1.2.0" in out
    assert "verified" in out


def test_capabilities_show_not_installed(capsys, saved_config):
    code = main(["capabilities", "show", "nonexistent"])
    assert code == 1


# --- Remove ---


def test_remove_not_installed(capsys, saved_config):
    code = main(["remove", "nonexistent"])
    assert code == 1
    out = capsys.readouterr().out
    assert "not installed" in out


def test_remove_with_yes(capsys, saved_config, lockfile_with_packages, isolated_env):
    code = main(["remove", "pdf-reader-pack", "--yes"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Removed" in out

    lock_path = isolated_env / "agentnode.lock"
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    assert "pdf-reader-pack" not in data["packages"]
    assert "csv-analyzer-pack" in data["packages"]


# --- Run validation ---


def test_run_no_input(capsys):
    code = main(["run", "some-pack"])
    assert code == 1
    out = capsys.readouterr().out
    assert "No input provided" in out


def test_run_both_input_and_file(capsys):
    code = main(["run", "some-pack", "--input", '{"a":1}', "--file", "x.json"])
    assert code == 1
    err = capsys.readouterr().err
    assert "mutually exclusive" in err


def test_run_invalid_json(capsys):
    code = main(["run", "some-pack", "--input", "not json"])
    assert code == 1
    err = capsys.readouterr().err
    assert "Invalid JSON" in err


def test_run_file_not_found(capsys):
    code = main(["run", "some-pack", "--file", "/nonexistent/file.json"])
    assert code == 1
    err = capsys.readouterr().err
    assert "File not found" in err


# --- Doctor ---


def test_doctor(capsys, saved_config):
    code = main(["doctor"])
    assert code == 0
    out = capsys.readouterr().out
    assert "AgentNode Doctor" in out
    assert "Config" in out
    assert "SDK" in out
    assert "Python" in out


# --- Reset ---


def test_reset_confirm_no(capsys, saved_config):
    with patch("builtins.input", return_value="n"):
        code = main(["reset"])
    assert code == 0
    from agentnode_sdk.config import config_exists

    assert config_exists() is True


def test_reset_confirm_yes(capsys, saved_config):
    with patch("builtins.input", return_value="y"):
        code = main(["reset"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Configuration reset" in out
    from agentnode_sdk.config import config_exists

    assert config_exists() is False


# --- No-color flag ---


def test_no_color_flag(capsys, saved_config):
    code = main(["--no-color", "config"])
    assert code == 0
    out = capsys.readouterr().out
    assert "\033[" not in out


# --- Auth commands ---


def test_auth_set(capsys, saved_config):
    with patch("agentnode_sdk.cli.auth.getpass.getpass", return_value="ghp_test_token_123"):
        code = main(["auth", "set", "github"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Credential stored for github" in out
    from agentnode_sdk.credential_store import has_credential
    assert has_credential("github") is True


def test_auth_set_empty_token(capsys, saved_config):
    with patch("agentnode_sdk.cli.auth.getpass.getpass", return_value=""):
        code = main(["auth", "set", "github"])
    assert code == 1
    err = capsys.readouterr().err
    assert "no token provided" in err


def test_auth_set_cancelled(capsys, saved_config):
    with patch("agentnode_sdk.cli.auth.getpass.getpass", side_effect=KeyboardInterrupt):
        code = main(["auth", "set", "github"])
    assert code == 130


def test_auth_list_empty(capsys, saved_config):
    code = main(["auth", "list"])
    assert code == 0
    out = capsys.readouterr().out
    assert "No credentials configured" in out


def test_auth_list_with_creds(capsys, saved_config):
    from agentnode_sdk.credential_store import set_credential
    set_credential("github", "ghp_xxx", auth_type="oauth2")
    set_credential("openai", "sk_yyy", auth_type="api_key")
    code = main(["auth", "list"])
    assert code == 0
    out = capsys.readouterr().out
    assert "github" in out
    assert "openai" in out
    assert "2 credential(s)" in out
    assert "ghp_xxx" not in out
    assert "sk_yyy" not in out


def test_auth_remove_exists(capsys, saved_config):
    from agentnode_sdk.credential_store import set_credential, has_credential
    set_credential("github", "ghp_xxx")
    code = main(["auth", "remove", "github"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Removed credential for github" in out
    assert has_credential("github") is False


def test_auth_remove_missing(capsys, saved_config):
    code = main(["auth", "remove", "nonexistent"])
    assert code == 1
    out = capsys.readouterr().out
    assert "No credential found for nonexistent" in out


def test_auth_status_no_packages(capsys, saved_config):
    code = main(["auth", "status"])
    assert code == 0
    out = capsys.readouterr().out
    assert "No installed packages require credentials" in out


def test_auth_status_with_connector(capsys, saved_config, isolated_env):
    import json
    from agentnode_sdk.credential_store import set_credential
    lock_path = isolated_env / "agentnode.lock"
    lock_data = {
        "lockfile_version": "0.1",
        "updated_at": "",
        "packages": {
            "gmail-pack": {
                "version": "1.0.0",
                "connector": {"provider": "google", "auth_type": "oauth2"},
                "capability_ids": ["email_sending"],
            },
            "openai-pack": {
                "version": "1.0.0",
                "connector": {"provider": "openai", "auth_type": "api_key"},
                "capability_ids": ["chat_completion"],
            },
        },
    }
    lock_path.write_text(json.dumps(lock_data), encoding="utf-8")
    set_credential("openai", "sk_test")

    import os
    os.environ["AGENTNODE_LOCKFILE"] = str(lock_path)
    try:
        code = main(["auth", "status"])
    finally:
        del os.environ["AGENTNODE_LOCKFILE"]

    assert code == 0
    out = capsys.readouterr().out
    assert "google" in out
    assert "missing" in out
    assert "openai" in out
    assert "configured" in out
    assert "agentnode auth set google" in out


def test_auth_bare_shows_list(capsys, saved_config):
    code = main(["auth"])
    assert code == 0
    out = capsys.readouterr().out
    assert "No credentials configured" in out


# --- Config enforcement in smart run ---


def _mock_resolve_result():
    """Create a mock resolve result with one package."""
    result = MagicMock()
    pkg = MagicMock()
    pkg.slug = "web-search-pack"
    pkg.score = 92.0
    pkg.trust_level = "verified"
    result.results = [pkg]
    return result


def test_smart_run_blocks_when_auto_upgrade_off(capsys, saved_config):
    """Smart run should refuse auto-install when auto_upgrade_policy is off."""
    from agentnode_sdk.config import load_config, save_config

    cfg = load_config()
    cfg["auto_upgrade_policy"] = "off"
    save_config(cfg)

    mock_client = MagicMock()
    mock_client.resolve.return_value = _mock_resolve_result()
    mock_client.close = MagicMock()

    with patch("agentnode_sdk.client.AgentNodeClient", return_value=mock_client):
        code = main(["run", "search", "for", "python", "tutorials"])

    assert code == 1
    out = capsys.readouterr().out
    assert "Auto-install disabled" in out


def test_smart_run_prompts_when_install_confirmation_prompt(capsys, saved_config):
    """Smart run should prompt user when install_confirmation is 'prompt'."""
    from agentnode_sdk.config import load_config, save_config

    cfg = load_config()
    cfg["install_confirmation"] = "prompt"
    save_config(cfg)

    mock_client = MagicMock()
    mock_client.resolve.return_value = _mock_resolve_result()
    mock_client.close = MagicMock()

    with patch("agentnode_sdk.client.AgentNodeClient", return_value=mock_client):
        with patch("builtins.input", return_value="n") as mock_input:
            code = main(["run", "search", "for", "python", "tutorials"])

    assert code == 0
    mock_input.assert_called_once()


def test_cmd_install_respects_trust_level(capsys, saved_config):
    """Direct install should pass trust level from config to client."""
    from agentnode_sdk.config import load_config, save_config

    cfg = load_config()
    cfg["trust"] = {"minimum_trust_level": "trusted"}
    save_config(cfg)

    mock_client = MagicMock()
    install_result = MagicMock()
    install_result.installed = True
    install_result.already_installed = False
    install_result.slug = "some-pack"
    install_result.version = "1.0.0"
    mock_client.install.return_value = install_result
    mock_client.close = MagicMock()

    with patch("agentnode_sdk.client.AgentNodeClient", return_value=mock_client):
        code = main(["install", "some-pack", "--yes"])

    assert code == 0
    mock_client.install.assert_called_once_with(
        "some-pack",
        version=None,
        require_verified=True,
        require_trusted=True,
    )


# --- Inspect ---


def test_inspect_installed(capsys, saved_config, lockfile_with_packages):
    code = main(["inspect", "pdf-reader-pack"])
    assert code == 0
    out = capsys.readouterr().out
    assert "pdf-reader-pack" in out
    assert "1.2.0" in out
    assert "verified" in out
    assert "Permissions" in out
    assert "Network" in out
    assert "Capabilities" in out
    assert "pdf_extraction" in out
    assert "Audit Summary" in out


def test_inspect_not_installed(capsys, saved_config):
    code = main(["inspect", "nonexistent"])
    assert code == 1
    err = capsys.readouterr().err
    assert "not installed" in err


def test_inspect_json(capsys, saved_config, lockfile_with_packages):
    code = main(["inspect", "pdf-reader-pack", "--json"])
    assert code == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["slug"] == "pdf-reader-pack"
    assert data["version"] == "1.2.0"
    assert data["trust_level"] == "verified"
    assert data["permissions"]["network_level"] == "none"
    assert "pdf_extraction" in data["capability_ids"]
    assert isinstance(data["audit"], dict)
    assert "total" in data["audit"]


def test_inspect_no_permissions(capsys, saved_config, lockfile_with_packages):
    """Package with permissions=None shows 'none declared'."""
    code = main(["inspect", "csv-analyzer-pack"])
    assert code == 0
    out = capsys.readouterr().out
    assert "csv-analyzer-pack" in out
    assert "none declared" in out


def test_inspect_with_audit_data(capsys, saved_config, lockfile_with_packages, isolated_env):
    """Audit summary should count entries for the inspected package."""
    from agentnode_sdk.config import config_dir

    audit_dir = config_dir()
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / "audit.jsonl"
    entries = [
        '{"slug":"pdf-reader-pack","action":"allow","event":"run_tool"}',
        '{"slug":"pdf-reader-pack","action":"allow","event":"run_tool"}',
        '{"slug":"pdf-reader-pack","action":"deny","event":"run_tool"}',
        '{"slug":"other-pack","action":"allow","event":"run_tool"}',
    ]
    audit_path.write_text("\n".join(entries) + "\n", encoding="utf-8")

    code = main(["inspect", "pdf-reader-pack"])
    assert code == 0
    out = capsys.readouterr().out
    assert "3" in out  # total for pdf-reader-pack
    assert "Deny" in out


def test_inspect_connector(capsys, saved_config, isolated_env):
    """Packages with connector info should show provider."""
    lock_path = isolated_env / "agentnode.lock"
    lock_data = {
        "lockfile_version": "0.1",
        "updated_at": "",
        "packages": {
            "slack-pack": {
                "version": "1.0.0",
                "package_type": "toolpack",
                "runtime": "python",
                "entrypoint": "slack.tool",
                "capability_ids": ["messaging"],
                "tools": [],
                "trust_level": "verified",
                "permissions": None,
                "connector": {"provider": "slack", "auth_type": "oauth2"},
            },
        },
    }
    lock_path.write_text(json.dumps(lock_data), encoding="utf-8")

    code = main(["inspect", "slack-pack"])
    assert code == 0
    out = capsys.readouterr().out
    assert "slack" in out
    assert "oauth2" in out


def test_inspect_shows_risk_level(capsys, saved_config, lockfile_with_packages):
    code = main(["inspect", "pdf-reader-pack"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Usage Risk" in out
    assert "Risk level" in out
    assert "Risk score" in out


def test_inspect_json_includes_risk(capsys, saved_config, lockfile_with_packages):
    code = main(["inspect", "pdf-reader-pack", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert "risk" in data
    assert data["risk"]["level"] in ("low", "medium", "high")
    assert isinstance(data["risk"]["score"], int)
    assert isinstance(data["risk"]["signals"], list)


# ---------------------------------------------------------------------------
# Inspect — integrity status (Phase 15.5)
# ---------------------------------------------------------------------------

def _write_lockfile_with_entry(env_path, slug, entry):
    from agentnode_sdk.installer import LOCKFILE_VERSION
    lock_path = env_path / "agentnode.lock"
    lock_path.write_text(json.dumps({
        "lockfile_version": LOCKFILE_VERSION,
        "updated_at": "2026-05-21T00:00:00+00:00",
        "packages": {slug: entry},
    }, indent=2), encoding="utf-8")


def test_inspect_integrity_verified(capsys, saved_config, isolated_env):
    from agentnode_sdk.lock_integrity import seal_entry
    entry = {
        "version": "1.0.0", "package_type": "toolpack", "runtime": "python",
        "entrypoint": "my_pack.tool", "artifact_hash": "sha256:abc",
        "tools": [], "permissions": {"network_level": "none"},
        "installed_at": "2026-05-21T00:00:00+00:00", "trust_level": "verified",
        "source": "sdk", "capability_ids": [],
        "prompts": [], "resources": [], "connector": None, "agent": None,
    }
    _write_lockfile_with_entry(isolated_env, "test-pack", seal_entry(entry))
    code = main(["inspect", "test-pack"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Integrity" in out
    assert "verified" in out


def test_inspect_integrity_missing(capsys, saved_config, isolated_env):
    entry = {
        "version": "1.0.0", "package_type": "toolpack", "runtime": "python",
        "entrypoint": "my_pack.tool", "artifact_hash": "sha256:abc",
        "tools": [], "permissions": {"network_level": "none"},
        "installed_at": "2026-05-21T00:00:00+00:00", "trust_level": "verified",
        "source": "sdk", "capability_ids": [],
        "prompts": [], "resources": [], "connector": None, "agent": None,
    }
    _write_lockfile_with_entry(isolated_env, "test-pack", entry)
    code = main(["inspect", "test-pack"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Integrity" in out
    assert "missing" in out
    assert "agentnode lock seal" in out


def test_inspect_integrity_mismatch(capsys, saved_config, isolated_env):
    from agentnode_sdk.lock_integrity import seal_entry
    entry = {
        "version": "1.0.0", "package_type": "toolpack", "runtime": "python",
        "entrypoint": "my_pack.tool", "artifact_hash": "sha256:abc",
        "tools": [], "permissions": {"network_level": "none"},
        "installed_at": "2026-05-21T00:00:00+00:00", "trust_level": "verified",
        "source": "sdk", "capability_ids": [],
        "prompts": [], "resources": [], "connector": None, "agent": None,
    }
    sealed = seal_entry(entry)
    sealed["entrypoint"] = "tampered.module"
    _write_lockfile_with_entry(isolated_env, "test-pack", sealed)
    code = main(["inspect", "test-pack"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Integrity" in out
    assert "MISMATCH" in out
    assert "agentnode lock verify" in out


def test_inspect_json_includes_integrity(capsys, saved_config, isolated_env):
    from agentnode_sdk.lock_integrity import seal_entry
    entry = {
        "version": "1.0.0", "package_type": "toolpack", "runtime": "python",
        "entrypoint": "my_pack.tool", "artifact_hash": "sha256:abc",
        "tools": [], "permissions": {"network_level": "none"},
        "installed_at": "2026-05-21T00:00:00+00:00", "trust_level": "verified",
        "source": "sdk", "capability_ids": [],
        "prompts": [], "resources": [], "connector": None, "agent": None,
    }
    _write_lockfile_with_entry(isolated_env, "test-pack", seal_entry(entry))
    code = main(["inspect", "test-pack", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert "integrity" in data
    assert data["integrity"]["status"] == "verified"
    assert data["integrity"]["algorithm"] == "sha256"
    assert data["integrity"]["canonical_version"] == 1


def test_inspect_json_integrity_missing(capsys, saved_config, isolated_env):
    entry = {
        "version": "1.0.0", "package_type": "toolpack", "runtime": "python",
        "entrypoint": "my_pack.tool", "artifact_hash": "sha256:abc",
        "tools": [], "permissions": {"network_level": "none"},
        "installed_at": "2026-05-21T00:00:00+00:00", "trust_level": "verified",
        "source": "sdk", "capability_ids": [],
        "prompts": [], "resources": [], "connector": None, "agent": None,
    }
    _write_lockfile_with_entry(isolated_env, "test-pack", entry)
    code = main(["inspect", "test-pack", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["integrity"]["status"] == "missing"
    assert "algorithm" not in data["integrity"]


# ---------------------------------------------------------------------------
# Inspect — signature (Phase 16.5)
# ---------------------------------------------------------------------------

def _sign_entry_for_inspect(slug, entry):
    """Sign an entry with a fresh Ed25519 key (test helper)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from agentnode_sdk.signature import build_sign_payload

    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    payload = build_sign_payload(slug, entry)
    sig_bytes = private_key.sign(payload)
    fingerprint = _hashlib.sha256(pub_bytes).hexdigest()[:16]

    return {
        "publisher": [{
            "key_id": f"ed25519:{fingerprint}",
            "algorithm": "ed25519",
            "signature": base64.b64encode(sig_bytes).decode(),
            "public_key": base64.b64encode(pub_bytes).decode(),
            "canonical_version": 1,
            "signed_at": "2026-05-21T12:00:00+00:00",
        }],
    }


def _make_inspect_entry(**overrides):
    """Standard inspect test entry."""
    entry = {
        "version": "1.0.0", "package_type": "toolpack", "runtime": "python",
        "entrypoint": "my_pack.tool", "artifact_hash": "sha256:abc",
        "tools": [], "permissions": {"network_level": "none"},
        "installed_at": "2026-05-21T00:00:00+00:00", "trust_level": "verified",
        "source": "sdk", "capability_ids": [],
        "prompts": [], "resources": [], "connector": None, "agent": None,
    }
    entry.update(overrides)
    return entry


def test_inspect_signature_valid(capsys, saved_config, isolated_env):
    from agentnode_sdk.lock_integrity import seal_entry
    entry = _make_inspect_entry()
    entry["_signatures"] = _sign_entry_for_inspect("test-pack", entry)
    _write_lockfile_with_entry(isolated_env, "test-pack", seal_entry(entry))
    code = main(["inspect", "test-pack"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Signature" in out
    assert "valid" in out


def test_inspect_signature_missing(capsys, saved_config, isolated_env):
    from agentnode_sdk.lock_integrity import seal_entry
    entry = _make_inspect_entry()
    _write_lockfile_with_entry(isolated_env, "test-pack", seal_entry(entry))
    code = main(["inspect", "test-pack"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Signature" in out
    assert "missing" in out


def test_inspect_signature_invalid(capsys, saved_config, isolated_env):
    from agentnode_sdk.lock_integrity import seal_entry
    entry = _make_inspect_entry()
    entry["_signatures"] = _sign_entry_for_inspect("test-pack", entry)
    entry["_signatures"]["publisher"][0]["signature"] = base64.b64encode(b"X" * 64).decode()
    _write_lockfile_with_entry(isolated_env, "test-pack", seal_entry(entry))
    code = main(["inspect", "test-pack"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Signature" in out
    assert "INVALID" in out


def test_inspect_json_signature_valid(capsys, saved_config, isolated_env):
    from agentnode_sdk.lock_integrity import seal_entry
    entry = _make_inspect_entry()
    entry["_signatures"] = _sign_entry_for_inspect("test-pack", entry)
    _write_lockfile_with_entry(isolated_env, "test-pack", seal_entry(entry))
    code = main(["inspect", "test-pack", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert "signature" in data
    assert data["signature"]["status"] == "valid"
    assert "key_id" in data["signature"]
    assert data["signature"]["algorithm"] == "ed25519"


def test_inspect_json_signature_missing(capsys, saved_config, isolated_env):
    from agentnode_sdk.lock_integrity import seal_entry
    entry = _make_inspect_entry()
    _write_lockfile_with_entry(isolated_env, "test-pack", seal_entry(entry))
    code = main(["inspect", "test-pack", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["signature"]["status"] == "missing"
    assert "algorithm" not in data["signature"]


def test_inspect_json_signature_invalid(capsys, saved_config, isolated_env):
    from agentnode_sdk.lock_integrity import seal_entry
    entry = _make_inspect_entry()
    entry["_signatures"] = _sign_entry_for_inspect("test-pack", entry)
    entry["_signatures"]["publisher"][0]["signature"] = base64.b64encode(b"X" * 64).decode()
    _write_lockfile_with_entry(isolated_env, "test-pack", seal_entry(entry))
    code = main(["inspect", "test-pack", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["signature"]["status"] == "invalid"
    assert "error" in data["signature"]


def test_inspect_no_local_signing_key_loaded(capsys, saved_config, isolated_env, monkeypatch):
    """inspect must not load or reference the local signing key."""
    from agentnode_sdk.lock_integrity import seal_entry
    entry = _make_inspect_entry()
    entry["_signatures"] = _sign_entry_for_inspect("test-pack", entry)
    _write_lockfile_with_entry(isolated_env, "test-pack", seal_entry(entry))
    import agentnode_sdk.signing_key as sk_mod
    monkeypatch.setattr(
        sk_mod, "load_signing_key",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("signing key loaded")),
    )
    code = main(["inspect", "test-pack"])
    assert code == 0


def test_inspect_publisher_slug_human(capsys, saved_config, isolated_env):
    from agentnode_sdk.lock_integrity import seal_entry
    entry = _make_inspect_entry(publisher_slug="acme-ai")
    _write_lockfile_with_entry(isolated_env, "test-pack", seal_entry(entry))
    code = main(["inspect", "test-pack"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Publisher" in out
    assert "acme-ai" in out


def test_inspect_publisher_slug_omitted_when_none(capsys, saved_config, isolated_env):
    from agentnode_sdk.lock_integrity import seal_entry
    entry = _make_inspect_entry()
    _write_lockfile_with_entry(isolated_env, "test-pack", seal_entry(entry))
    code = main(["inspect", "test-pack"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Publisher" not in out


def test_inspect_json_includes_publisher_slug(capsys, saved_config, isolated_env):
    from agentnode_sdk.lock_integrity import seal_entry
    entry = _make_inspect_entry(publisher_slug="acme-ai")
    _write_lockfile_with_entry(isolated_env, "test-pack", seal_entry(entry))
    code = main(["inspect", "test-pack", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["publisher_slug"] == "acme-ai"


def test_inspect_json_publisher_slug_null_when_absent(capsys, saved_config, isolated_env):
    from agentnode_sdk.lock_integrity import seal_entry
    entry = _make_inspect_entry()
    _write_lockfile_with_entry(isolated_env, "test-pack", seal_entry(entry))
    code = main(["inspect", "test-pack", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["publisher_slug"] is None
