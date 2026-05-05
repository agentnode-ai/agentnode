"""Tests for agentnode_sdk CLI."""
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
    assert "0.4.1" in out


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
