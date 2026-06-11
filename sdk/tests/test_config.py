"""Tests for agentnode_sdk.config."""
import json
import os
from pathlib import Path

import pytest

from agentnode_sdk.config import (
    DEFAULTS,
    config_exists,
    config_path,
    default_config,
    delete_config,
    get_value,
    installation_behavior_label,
    load_config,
    save_config,
    set_value,
)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point config to a temp directory for every test."""
    cfg_file = tmp_path / "config.json"
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    return cfg_file


# --- default_config ---


def test_default_config_has_required_keys():
    cfg = default_config()
    assert cfg["version"] == "1"
    assert cfg["auto_upgrade_policy"] == "safe"
    assert cfg["install_confirmation"] == "auto"
    assert cfg["trust"]["minimum_trust_level"] == "verified"
    assert "allow_unverified" not in cfg["trust"]
    assert cfg["permissions"]["network"] == "prompt"
    assert cfg["permissions"]["filesystem"] == "prompt"
    assert cfg["permissions"]["code_execution"] == "sandboxed"
    assert cfg["credentials"]["require_before_auto_install"] is True
    assert cfg["created_at"]
    assert cfg["updated_at"]


# --- save / load round-trip ---


def test_save_and_load_roundtrip(isolated_config):
    cfg = default_config()
    cfg["auto_upgrade_policy"] = "off"
    save_config(cfg)

    loaded = load_config()
    assert loaded["auto_upgrade_policy"] == "off"
    assert loaded["version"] == "1"


def test_load_missing_file_returns_defaults():
    cfg = load_config()
    assert cfg["auto_upgrade_policy"] == "safe"


def test_load_corrupt_json_returns_defaults(isolated_config):
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text("{bad json!", encoding="utf-8")
    cfg = load_config()
    assert cfg["auto_upgrade_policy"] == "safe"


def test_load_non_dict_returns_defaults(isolated_config):
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text('"just a string"', encoding="utf-8")
    cfg = load_config()
    assert cfg["auto_upgrade_policy"] == "safe"


def test_load_partial_config_fills_defaults(isolated_config):
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text(
        json.dumps({"auto_upgrade_policy": "off"}), encoding="utf-8"
    )
    cfg = load_config()
    assert cfg["auto_upgrade_policy"] == "off"
    assert cfg["permissions"]["network"] == "prompt"  # filled from defaults
    assert cfg["credentials"]["require_before_auto_install"] is True  # filled from defaults


def test_load_preserves_credentials_config(isolated_config):
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text(
        json.dumps({"credentials": {"require_before_auto_install": False}}),
        encoding="utf-8",
    )
    cfg = load_config()
    assert cfg["credentials"]["require_before_auto_install"] is False


# --- config_exists / delete ---


def test_config_exists_false_initially():
    assert config_exists() is False


def test_config_exists_after_save():
    save_config(default_config())
    assert config_exists() is True


def test_delete_config():
    save_config(default_config())
    assert delete_config() is True
    assert config_exists() is False


def test_delete_config_missing():
    assert delete_config() is False


# --- get_value ---


def test_get_value_top_level():
    cfg = default_config()
    assert get_value(cfg, "auto_upgrade_policy") == "safe"


def test_get_value_nested():
    cfg = default_config()
    assert get_value(cfg, "trust.minimum_trust_level") == "verified"
    assert get_value(cfg, "permissions.network") == "prompt"
    assert get_value(cfg, "credentials.require_before_auto_install") is True


def test_get_value_unknown_key():
    cfg = default_config()
    with pytest.raises(KeyError, match="Unknown config key"):
        get_value(cfg, "nonexistent.key")


# --- set_value ---


def test_set_value_top_level():
    cfg = default_config()
    set_value(cfg, "auto_upgrade_policy", "off")
    assert cfg["auto_upgrade_policy"] == "off"


def test_set_value_nested():
    cfg = default_config()
    set_value(cfg, "permissions.network", "allow")
    assert cfg["permissions"]["network"] == "allow"


def test_set_value_boolean():
    cfg = default_config()
    set_value(cfg, "credentials.require_before_auto_install", "false")
    assert cfg["credentials"]["require_before_auto_install"] is False


def test_set_value_unknown_key():
    cfg = default_config()
    with pytest.raises(KeyError, match="Unknown config key"):
        set_value(cfg, "unknown_key", "value")


def test_set_value_invalid_value():
    cfg = default_config()
    with pytest.raises(ValueError, match="Invalid value"):
        set_value(cfg, "auto_upgrade_policy", "invalid")


def test_set_value_validates_all_keys():
    cfg = default_config()

    set_value(cfg, "auto_upgrade_policy", "safe")
    set_value(cfg, "auto_upgrade_policy", "off")

    set_value(cfg, "install_confirmation", "auto")
    set_value(cfg, "install_confirmation", "prompt")

    set_value(cfg, "trust.minimum_trust_level", "verified")
    set_value(cfg, "trust.minimum_trust_level", "trusted")
    set_value(cfg, "trust.minimum_trust_level", "curated")

    set_value(cfg, "permissions.network", "allow")
    set_value(cfg, "permissions.network", "prompt")
    set_value(cfg, "permissions.network", "deny")

    set_value(cfg, "permissions.filesystem", "allow")
    set_value(cfg, "permissions.code_execution", "sandboxed")
    set_value(cfg, "permissions.code_execution", "prompt")
    set_value(cfg, "permissions.code_execution", "deny")

    set_value(cfg, "credentials.require_before_auto_install", "true")
    set_value(cfg, "credentials.require_before_auto_install", "false")


def test_set_value_credentials_boolean():
    cfg = default_config()
    set_value(cfg, "credentials.require_before_auto_install", "false")
    assert cfg["credentials"]["require_before_auto_install"] is False
    set_value(cfg, "credentials.require_before_auto_install", "true")
    assert cfg["credentials"]["require_before_auto_install"] is True


# --- installation_behavior_label ---


def test_label_automatic():
    cfg = {"auto_upgrade_policy": "safe", "install_confirmation": "auto"}
    assert installation_behavior_label(cfg) == "automatic"


def test_label_review():
    cfg = {"auto_upgrade_policy": "safe", "install_confirmation": "prompt"}
    assert installation_behavior_label(cfg) == "review before install"


def test_label_manual():
    cfg = {"auto_upgrade_policy": "off", "install_confirmation": "auto"}
    assert installation_behavior_label(cfg) == "manual only"


def test_label_manual_ignores_confirmation():
    cfg = {"auto_upgrade_policy": "off", "install_confirmation": "prompt"}
    assert installation_behavior_label(cfg) == "manual only"


# --- config_path override ---


def test_config_path_env_override(tmp_path, monkeypatch):
    custom = tmp_path / "custom" / "my_config.json"
    monkeypatch.setenv("AGENTNODE_CONFIG", str(custom))
    assert config_path() == custom


def test_config_path_env_dir_override(tmp_path, monkeypatch):
    custom_dir = tmp_path / "custom_dir"
    monkeypatch.setenv("AGENTNODE_CONFIG", str(custom_dir))
    assert config_path() == custom_dir / "config.json"


# --- agent_sandbox section (0.12.1 hotfix: _merge_defaults must not strip it) ---


class TestAgentSandboxConfigSection:
    """Regression tests for the shipped 0.12.0 bug: a hand-written
    agent_sandbox section was silently stripped by _merge_defaults, so the
    documented config path (agent_sandbox.enabled) and the C2 host LLM
    ceiling (agent_sandbox.llm) never reached their consumers."""

    def test_defaults_include_agent_sandbox_disabled(self):
        cfg = default_config()
        assert cfg["agent_sandbox"]["enabled"] is False

    def test_handwritten_section_survives_load(self, isolated_config):
        isolated_config.parent.mkdir(parents=True, exist_ok=True)
        isolated_config.write_text(
            json.dumps({"agent_sandbox": {"enabled": True}}), encoding="utf-8")
        loaded = load_config()
        assert loaded["agent_sandbox"]["enabled"] is True

    def test_nested_llm_ceiling_survives_load(self, isolated_config):
        llm = {"enabled": True, "max_calls": 3, "max_input_chars": 1000,
               "allowed_models": ["gpt-4o-mini"]}
        isolated_config.parent.mkdir(parents=True, exist_ok=True)
        isolated_config.write_text(
            json.dumps({"agent_sandbox": {"enabled": True, "llm": llm}}),
            encoding="utf-8")
        loaded = load_config()
        assert loaded["agent_sandbox"]["llm"] == llm

    def test_save_load_roundtrip_preserves_section(self, isolated_config):
        cfg = default_config()
        cfg["agent_sandbox"] = {"enabled": True, "llm": {"max_calls": 5}}
        save_config(cfg)
        loaded = load_config()
        assert loaded["agent_sandbox"]["enabled"] is True
        assert loaded["agent_sandbox"]["llm"] == {"max_calls": 5}

    def test_set_value_true_stores_real_bool(self):
        cfg = default_config()
        set_value(cfg, "agent_sandbox.enabled", "true")
        assert cfg["agent_sandbox"]["enabled"] is True

    def test_set_value_false_stores_real_bool_not_truthy_string(self):
        # without the bool_keys entry this stored the string "false",
        # which bool() would read as ENABLED
        cfg = default_config()
        set_value(cfg, "agent_sandbox.enabled", "false")
        assert cfg["agent_sandbox"]["enabled"] is False
        assert cfg["agent_sandbox"]["enabled"] is not True
        assert not isinstance(cfg["agent_sandbox"]["enabled"], str)

    def test_set_value_rejects_garbage(self):
        cfg = default_config()
        with pytest.raises(ValueError):
            set_value(cfg, "agent_sandbox.enabled", "maybe")

    def test_flag_enabled_via_config_file_alone(self, isolated_config, monkeypatch):
        # the REAL integration: file on disk -> load_config -> flag reader
        # (the old routing tests mocked load_config, which hid this bug)
        from agentnode_sdk.runtimes.agent_sandbox import _agent_sandbox_enabled
        monkeypatch.delenv("AGENTNODE_AGENT_SANDBOX", raising=False)
        isolated_config.parent.mkdir(parents=True, exist_ok=True)
        isolated_config.write_text(
            json.dumps({"agent_sandbox": {"enabled": True}}), encoding="utf-8")
        assert _agent_sandbox_enabled() is True

    def test_flag_disabled_via_config_file_false(self, isolated_config, monkeypatch):
        from agentnode_sdk.runtimes.agent_sandbox import _agent_sandbox_enabled
        monkeypatch.delenv("AGENTNODE_AGENT_SANDBOX", raising=False)
        isolated_config.parent.mkdir(parents=True, exist_ok=True)
        isolated_config.write_text(
            json.dumps({"agent_sandbox": {"enabled": False}}), encoding="utf-8")
        assert _agent_sandbox_enabled() is False

    def test_llm_ceiling_reaches_policy_resolution(self, isolated_config):
        # file on disk -> load_config -> resolve_llm_policy: the host ceiling
        # must clamp a greedy manifest (dead config before this fix)
        from agentnode_sdk.runtimes.agent_llm_policy import resolve_llm_policy
        isolated_config.parent.mkdir(parents=True, exist_ok=True)
        isolated_config.write_text(
            json.dumps({"agent_sandbox": {"llm": {"max_calls": 2}}}),
            encoding="utf-8")
        host_cfg = load_config()
        pol = resolve_llm_policy(
            {"llm_access": {"enabled": True, "max_calls": 999}}, host_cfg)
        assert pol.max_calls == 2

    def test_other_sections_unchanged(self, isolated_config):
        isolated_config.parent.mkdir(parents=True, exist_ok=True)
        isolated_config.write_text(
            json.dumps({"guard": {"delete": "deny"},
                        "agent_sandbox": {"enabled": True}}), encoding="utf-8")
        loaded = load_config()
        assert loaded["guard"]["delete"] == "deny"
        assert loaded["guard"]["read"] == "allow"          # defaults intact
        assert loaded["trust"]["minimum_trust_level"] == "verified"


# --- llm.providers section (Endpoint-A) ---


class TestLlmProvidersConfigSection:
    def test_defaults_include_empty_providers(self):
        cfg = default_config()
        assert cfg["llm"]["providers"] == {}

    def test_providers_survive_load(self, isolated_config):
        providers = {"myvllm": {"base_url": "http://10.0.0.5:8000/v1",
                                "model": "llama", "requires_key": False}}
        isolated_config.parent.mkdir(parents=True, exist_ok=True)
        isolated_config.write_text(
            json.dumps({"llm": {"default_provider": "deepseek",
                                "providers": providers}}), encoding="utf-8")
        loaded = load_config()
        assert loaded["llm"]["providers"] == providers
        assert loaded["llm"]["default_provider"] == "deepseek"

    def test_set_value_accepts_new_provider_names(self):
        cfg = default_config()
        for name in ("deepseek", "mistral", "qwen", "ollama"):
            set_value(cfg, "llm.default_provider", name)
            assert cfg["llm"]["default_provider"] == name

    def test_set_value_rejects_garbage_provider(self):
        cfg = default_config()
        with pytest.raises(ValueError):
            set_value(cfg, "llm.default_provider", "skynet")
