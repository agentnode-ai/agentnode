"""agentnode auth — credential management CLI.

Secrets are entered via getpass (never argv, never echoed) and only ever
shown masked (last 4 chars). Storage labels are honest: "OS keychain" or
"plaintext file" — never "encrypted"."""
from __future__ import annotations

import getpass
import os
import sys

from agentnode_sdk.credential_store import (
    has_credential,
    list_credentials,
    load_credentials,
    remove_credential,
    set_credential,
)
from agentnode_sdk.installer import read_lockfile
from agentnode_sdk.cli.output import bold, dim, section


def _masked(token: str) -> str:
    """Only ever show the last 4 chars of a secret."""
    return "..." + token[-4:] if len(token) >= 8 else "..."


def _storage_short(storage: str) -> str:
    return "OS keychain" if storage == "keyring" else "plaintext file"


def _host_config() -> dict:
    try:
        from agentnode_sdk.config import load_config
        return load_config() or {}
    except Exception:
        return {}


def _llm_effective_source(provider: str, host_config: dict | None = None) -> tuple[str | None, str]:
    """(effective key or None, human-readable source) for an LLM provider.

    Mirrors the runtime's resolution: env (incl. ~/.agentnode/.env) overrides
    the stored credential. Env var names come from the provider REGISTRY
    (single source of truth). Uses metadata only for the stored check here —
    the real keychain read happens in ``auth test``."""
    from agentnode_sdk.llm_providers import resolve_provider_spec
    from agentnode_sdk.runtimes.agent_runner import _load_agentnode_env
    _load_agentnode_env()

    spec = resolve_provider_spec(provider, host_config) or {}
    env_var = spec.get("api_key_env") or ""
    env_key = (os.environ.get(env_var) or None) if env_var else None
    meta = load_credentials().get("providers", {}).get(provider)
    stored = isinstance(meta, dict)
    storage = (meta or {}).get("storage", "file")

    if env_key and stored:
        return env_key, f"env var {env_var} — overrides stored credential"
    if env_key:
        return env_key, f"env var {env_var}"
    if stored:
        return None, _storage_short(storage)
    return None, "not configured"


def _registry_key_providers(host_config: dict | None) -> list[str]:
    """Key-requiring providers from the registry (presets + custom config
    entries), in stable registry order."""
    from agentnode_sdk.llm_providers import known_provider_names, resolve_provider_spec

    names: list[str] = []
    for name in known_provider_names(host_config):
        spec = resolve_provider_spec(name, host_config)
        if spec and spec.get("requires_key", True):
            names.append(name)
    return names


def _resolve_auth_type(provider: str) -> str:
    """Read auth_type from lockfile connector entry. Falls back to 'api_key'.

    If multiple installed connectors share a provider with different auth
    types, this returns the first match — may need disambiguation later.
    """
    lock = read_lockfile()
    for _slug, info in lock.get("packages", {}).items():
        connector = info.get("connector")
        if isinstance(connector, dict) and connector.get("provider", "").lower() == provider:
            return connector.get("auth_type", "api_key")
    return "api_key"


def cmd_auth_set(provider: str, from_env: str | None = None) -> int:
    provider = provider.lower().strip()
    if not provider:
        print("  Error: provider name required.", file=sys.stderr)
        return 1
    if from_env:
        # Import an existing env key (also honors ~/.agentnode/.env). A flag,
        # not a prompt — keeps piped-stdin flows intact.
        from agentnode_sdk.runtimes.agent_runner import _load_agentnode_env
        _load_agentnode_env()
        token = (os.environ.get(from_env) or "").strip()
        if not token:
            print(f"  Error: environment variable {from_env} is not set.", file=sys.stderr)
            return 1
    else:
        try:
            token = getpass.getpass(f"  Enter access token for {provider}: ")
        except (KeyboardInterrupt, EOFError):
            print("\n  Cancelled.")
            return 130
        token = token.strip()
        if not token:
            print("  Error: no token provided.", file=sys.stderr)
            return 1
    auth_type = _resolve_auth_type(provider)
    storage = set_credential(provider, token, auth_type=auth_type)
    if storage == "keyring":
        where = "OS keychain"
    else:
        where = "plaintext file (0600) — OS keychain unavailable on this system"
    print(f"\n  Credential stored for {provider} ({_masked(token)}) — storage: {where}.\n")
    return 0


def cmd_auth_list() -> int:
    creds = list_credentials()
    if not creds:
        print("\n  No credentials configured.")
        print(dim("  Run `agentnode auth set <provider>` to store a credential.\n"))
        return 0
    print()
    print(section("Credentials"))
    print(f"  {'Provider':<14}{'Auth type':<13}{'Storage':<16}{'Stored'}")
    print(f"  {'--------':<14}{'--------':<13}{'-------':<16}{'------'}")
    for provider, info in sorted(creds.items()):
        stored = info.get("stored_at", "")[:10]
        auth_type = info.get("auth_type", "unknown")
        storage = _storage_short(info.get("storage", "file"))
        print(f"  {provider:<14}{auth_type:<13}{storage:<16}{stored}")
    print(f"\n  {len(creds)} credential(s) configured.\n")
    return 0


def cmd_auth_remove(provider: str) -> int:
    provider = provider.lower().strip()
    if remove_credential(provider):
        print(f"\n  Removed credential for {provider}.\n")
        return 0
    print(f"\n  No credential found for {provider}.\n")
    return 1


def cmd_auth_status() -> int:
    # --- LLM providers (host runtime / sandbox broker) -----------------------
    host_cfg = _host_config()
    print()
    print(section("LLM Providers"))
    print(f"  {'Provider':<14}{'Status':<16}{'Effective source'}")
    print(f"  {'--------':<14}{'------':<16}{'----------------'}")
    for provider in _registry_key_providers(host_cfg):
        key, source = _llm_effective_source(provider, host_cfg)
        configured = key is not None or source not in ("not configured",)
        status = "configured" if configured else "missing"
        print(f"  {provider:<14}{status:<16}{source}")
    # ollama: keyless local provider — selection is config, not a credential
    llm_cfg = host_cfg.get("llm") or {}
    if llm_cfg.get("default_provider") == "ollama":
        ollama_source = "selected via llm.default_provider"
    elif "ollama" in (llm_cfg.get("providers") or {}):
        ollama_source = "configured via llm.providers"
    else:
        ollama_source = "not selected — agentnode config set llm.default_provider ollama"
    print(f"  {'ollama':<14}{'local (keyless)':<16}{ollama_source}")
    print(dim("  Env vars always override stored credentials. "
              "Test a key: agentnode auth test <provider>"))

    # --- connector packages (unchanged) --------------------------------------
    lock = read_lockfile()
    pkgs = lock.get("packages", {})

    # Collect provider → list of slugs
    provider_packages: dict[str, list[str]] = {}
    for slug, info in pkgs.items():
        connector = info.get("connector")
        if isinstance(connector, dict) and connector.get("provider"):
            provider = connector["provider"].lower().strip()
            provider_packages.setdefault(provider, []).append(slug)

    if not provider_packages:
        print("\n  No installed packages require connector credentials.\n")
        return 0

    print()
    print(section("Credential Status"))
    print(f"  {'Provider':<14}{'Status':<16}{'Used by'}")
    print(f"  {'--------':<14}{'------':<16}{'------'}")
    missing: list[str] = []
    for provider in sorted(provider_packages):
        configured = has_credential(provider)
        status = "configured" if configured else "missing"
        slugs = ", ".join(sorted(provider_packages[provider]))
        print(f"  {provider:<14}{status:<16}{slugs}")
        if not configured:
            missing.append(provider)

    if missing:
        print()
        print(dim("  Fix missing:"))
        for p in missing:
            print(dim(f"    agentnode auth set {p}"))
    print()
    return 0


# Free, cost-less validation endpoints (no completion call, no charge).
# GENERIC rule for OpenAI-compatible providers: GET {base_url}/models with a
# Bearer token (authenticated on deepseek/mistral/qwen/gemini and most custom
# endpoints). EXCEPTIONS: openai (official URL), anthropic (REQUIRES the
# anthropic-version header — a 400 without it would be misread as a bad key),
# openrouter (its /models is UNAUTHENTICATED, validates nothing — /auth/key is
# the correct probe).
_TEST_EXCEPTIONS = {
    "openai": lambda key, spec: (
        "https://api.openai.com/v1/models",
        {"Authorization": f"Bearer {key}"},
    ),
    "anthropic": lambda key, spec: (
        "https://api.anthropic.com/v1/models",
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
    ),
    "openrouter": lambda key, spec: (
        "https://openrouter.ai/api/v1/auth/key",
        {"Authorization": f"Bearer {key}"},
    ),
}


def _test_request(provider: str, key: str | None, spec: dict) -> tuple[str, dict] | None:
    if provider in _TEST_EXCEPTIONS:
        return _TEST_EXCEPTIONS[provider](key, spec)
    base = (spec.get("base_url") or "").rstrip("/")
    if not base:
        return None  # no compat endpoint to probe
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return (base + "/models", headers)


def cmd_auth_test(provider: str) -> int:
    """Validate the EFFECTIVE key for an LLM provider (env beats vault) via a
    free endpoint — registry-driven, so custom ``llm.providers`` entries work
    too. Exit codes: 0 valid (key providers) / reachable (keyless) · 1
    rejected (401/403, key providers only) · 2 not configured or unsupported ·
    3 indeterminate (network/5xx — never reported as invalid; for keyless
    providers this means unreachable). Never prints the key (masked last-4
    only), never echoes provider response bodies (they can contain key
    fragments), never starts anything."""
    provider = provider.lower().strip()
    host_cfg = _host_config()
    from agentnode_sdk.llm_providers import resolve_provider_spec
    spec = resolve_provider_spec(provider, host_cfg)
    if spec is None:
        print(f"  Unknown provider '{provider}'. Known: see `agentnode auth status`; "
              "custom endpoints go under llm.providers.<name>.", file=sys.stderr)
        return 2

    requires_key = spec.get("requires_key", True)
    if requires_key:
        key, source = _llm_effective_source(provider, host_cfg)
        if key is None:
            # No env override — resolve the stored credential (real keychain read).
            from agentnode_sdk.credential_store import get_llm_api_key
            key = get_llm_api_key(provider)
        if not key:
            print(f"\n  {provider}: not configured. Run `agentnode auth set {provider}`.\n")
            return 2
        shown = f"{_masked(key)}, {source}"
    else:
        key, shown = None, "local (keyless)"

    req = _test_request(provider, key, spec)
    if req is None:
        print(f"\n  {provider}: no endpoint to probe (no base_url configured).\n")
        return 2
    url, headers = req

    import httpx
    try:
        resp = httpx.get(url, headers=headers, timeout=10.0)
    except Exception:
        if not requires_key:
            print(f"\n  {provider} ({shown}): could not reach {url} — "
                  "is the local server (e.g. Ollama) running?\n")
        else:
            print(f"\n  {provider} ({shown}): could not reach the "
                  "provider — key validity unknown.\n")
        return 3
    if resp.status_code == 200:
        if requires_key:
            print(f"\n  {provider} ({shown}): key is valid.\n")
        else:
            print(f"\n  {provider} ({shown}): endpoint is reachable.\n")
        return 0
    if requires_key and resp.status_code in (401, 403):
        print(f"\n  {provider} ({shown}): key was rejected by "
              f"the provider (HTTP {resp.status_code}).\n")
        return 1
    print(f"\n  {provider} ({shown}): unexpected response "
          f"(HTTP {resp.status_code}) — status unknown.\n")
    return 3
