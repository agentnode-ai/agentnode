"""Generate a directly-usable generic MCP-client configuration (Slice 2, U4/U5).

The product CORE emits ONE vendor-neutral server spec -- ``{command, args, env}`` that
launches the capability reverse adapter over stdio. The generic config wraps it in the
de-facto ``{"mcpServers": {...}}`` object most MCP clients accept. Claude Code and Codex
examples are RENDERED from that same generic spec here; they add no vendor-specific
behaviour to the product core -- they are documentation/examples, and the capability
contract/execution/manifest code has no vendor branching.

Structural validators check a config is well-formed, points at the generic adapter and
declares NO secrets. A real Claude Code / Codex client run is a separate, explicitly
measured thing -- these validators do not claim one.
"""
from __future__ import annotations

import json
from typing import Any

ADAPTER_MODULE = "agentnode_sdk.capability.usability_adapter"


def generic_server_spec(stand: str, *, python: str = "python") -> dict[str, Any]:
    """The one vendor-neutral spec: how any MCP client launches the capability adapter over stdio."""
    return {
        "command": python,
        "args": ["-m", ADAPTER_MODULE, stand],
        "env": {},
    }


def generic_mcp_config(stand: str, *, name: str = "agentnode-capability",
                       python: str = "python") -> dict[str, Any]:
    """A directly-usable generic MCP client config (the widely-accepted mcpServers object)."""
    return {"mcpServers": {name: generic_server_spec(stand, python=python)}}


def claude_code_config(stand: str, *, name: str = "agentnode-capability",
                       python: str = "python") -> dict[str, Any]:
    """A Claude Code ``.mcp.json`` example -- the same generic mcpServers object (documentation)."""
    return generic_mcp_config(stand, name=name, python=python)


def codex_config_toml(stand: str, *, name: str = "agentnode-capability",
                      python: str = "python") -> str:
    """A Codex ``~/.codex/config.toml`` example rendered from the same generic spec (documentation)."""
    spec = generic_server_spec(stand, python=python)
    args = ", ".join(json.dumps(a) for a in spec["args"])
    return (
        f"[mcp_servers.{name}]\n"
        f"command = {json.dumps(spec['command'])}\n"
        f"args = [{args}]\n"
        f"env = {{}}\n"
    )


# -- structural validation (NOT a claim of a real client run) ------------------ #
def _validate_server_spec(spec: Any) -> list[str]:
    errs: list[str] = []
    if not isinstance(spec, dict):
        return ["server spec must be an object"]
    if not isinstance(spec.get("command"), str) or not spec["command"]:
        errs.append("command must be a non-empty string")
    args = spec.get("args")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        errs.append("args must be a list of strings")
    elif "-m" not in args or ADAPTER_MODULE not in args:
        errs.append(f"args must launch the generic adapter ({ADAPTER_MODULE})")
    env = spec.get("env", {})
    if not isinstance(env, dict):
        errs.append("env must be an object")
    elif env:
        errs.append("env must declare NO secrets (expected empty for this credential-free capability)")
    return errs


def validate_generic_config(cfg: Any) -> list[str]:
    """Return structural problems with a generic mcpServers config; empty means well-formed."""
    if not isinstance(cfg, dict) or not isinstance(cfg.get("mcpServers"), dict) or not cfg["mcpServers"]:
        return ["config must be an object with a non-empty mcpServers object"]
    errs: list[str] = []
    for name, spec in cfg["mcpServers"].items():
        errs += [f"mcpServers.{name}: {e}" for e in _validate_server_spec(spec)]
    return errs


def validate_claude_code_config(cfg: Any) -> list[str]:
    """Claude Code uses the same .mcp.json mcpServers shape."""
    return validate_generic_config(cfg)


def validate_codex_config_toml(text: str) -> list[str]:
    """Structural check of the Codex TOML example (parses with tomllib when available)."""
    if not isinstance(text, str) or not text.strip():
        return ["codex config must be non-empty TOML text"]
    try:
        import tomllib  # py3.11+
    except ImportError:
        ok = ("[mcp_servers." in text and "command =" in text and "args =" in text
              and ADAPTER_MODULE in text)
        return [] if ok else ["codex TOML missing [mcp_servers.*] / command / args / adapter module"]
    try:
        data = tomllib.loads(text)
    except Exception as exc:  # noqa: BLE001
        return [f"codex TOML does not parse: {type(exc).__name__}: {exc}"]
    servers = data.get("mcp_servers")
    if not isinstance(servers, dict) or not servers:
        return ["codex TOML must define [mcp_servers.<name>]"]
    errs: list[str] = []
    for name, spec in servers.items():
        if not isinstance(spec.get("command"), str) or not spec["command"]:
            errs.append(f"mcp_servers.{name}.command must be a non-empty string")
        args = spec.get("args")
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            errs.append(f"mcp_servers.{name}.args must be a list of strings")
        elif ADAPTER_MODULE not in args:
            errs.append(f"mcp_servers.{name}.args must launch the generic adapter ({ADAPTER_MODULE})")
        if spec.get("env") not in (None, {}):
            errs.append(f"mcp_servers.{name}.env must declare NO secrets (empty)")
    return errs
