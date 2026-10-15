"""Generic / Claude Code / Codex client-config generation + structural validation (U4/U5)."""
from __future__ import annotations

from agentnode_sdk.capability import client_config as cc


def test_generic_config_is_well_formed_and_points_at_adapter():
    cfg = cc.generic_mcp_config("./stand", name="agentnode-capability")
    assert cc.validate_generic_config(cfg) == []
    spec = cfg["mcpServers"]["agentnode-capability"]
    assert spec["args"][:2] == ["-m", cc.ADAPTER_MODULE]
    assert spec["args"][-1] == "./stand"
    assert spec["env"] == {}


def test_claude_code_config_matches_generic_shape():
    cfg = cc.claude_code_config("./stand")
    assert cc.validate_claude_code_config(cfg) == []
    assert "mcpServers" in cfg


def test_codex_toml_validates():
    text = cc.codex_config_toml("./stand", name="agentnode-capability")
    assert "[mcp_servers.agentnode-capability]" in text
    assert cc.validate_codex_config_toml(text) == []


def test_validator_rejects_secrets_in_env():
    bad = {"mcpServers": {"x": {"command": "python",
                                "args": ["-m", cc.ADAPTER_MODULE, "./stand"],
                                "env": {"TOKEN": "secret"}}}}
    errs = cc.validate_generic_config(bad)
    assert any("NO secrets" in e for e in errs)


def test_validator_rejects_wrong_module():
    bad = {"mcpServers": {"x": {"command": "python", "args": ["-m", "something.else"], "env": {}}}}
    assert cc.validate_generic_config(bad)


def test_codex_validator_rejects_missing_adapter():
    assert cc.validate_codex_config_toml("[mcp_servers.x]\ncommand = \"python\"\nargs = [\"-m\", \"other\"]\n")
