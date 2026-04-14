"""Tests for agentnode_sdk CLI."""
import json
from pathlib import Path
from unittest.mock import patch

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
    assert "Config file" in out
    assert "found" in out
    assert "SDK version" in out
    assert "Python version" in out


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
