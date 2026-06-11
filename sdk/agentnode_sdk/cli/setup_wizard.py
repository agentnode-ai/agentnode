"""AgentNode setup wizard — interactive configuration.

UX-3A: the wizard offers (never requires) storing LLM provider keys via the
credential vault. All credential handling reuses the existing
auth/credential_store primitives: keys only via getpass or an env-var import
(never argv), output only masked, honest storage labels (never "encrypted"),
key tests non-blocking.

UX-3B: a "Local sandbox" screen reuses the doctor's read-only diagnosis. The
ONLY operative action is an image pull through the existing, fully guarded
``cmd_sandbox_pull`` — offered solely on a TTY after an explicit Yes (default
No), and a pull failure never fails the wizard. The agent-sandbox flag is
offered ONLY when the sandbox is fully ready (default No) and is persisted via
the wizard's normal Save confirm. The wizard itself contains no docker calls.
"""
from __future__ import annotations

from agentnode_sdk.config import (
    default_config,
    installation_behavior_label,
    save_config,
)
from agentnode_sdk.cli.output import bold, dim, kv, section

_LLM_CHOICES = {"1": "openai", "2": "anthropic", "3": "openrouter"}


def run_wizard() -> int:
    """Run the setup wizard. Returns exit code."""
    try:
        cfg = _wizard_flow()
        return 0 if cfg is not None else 1
    except (KeyboardInterrupt, EOFError):
        print("\n\nSetup cancelled.")
        return 130


def _prompt(text: str, default: str = "") -> str:
    result = input(text).strip()
    return result if result else default


def _choice(prompt_text: str, options: list[str], default: str = "1") -> str:
    result = _prompt(f"{prompt_text} [{default}]: ", default)
    if result not in options:
        print(f"  Invalid choice. Using default: {default}")
        return default
    return result


def _store_llm_key(provider: str) -> str | None:
    """Store one provider key via the vault. Returns the short storage label
    ("OS keychain"/"plaintext file") on success, None when skipped.

    Key entry is getpass-only (hidden, never argv); if the provider's env var
    is already set (incl. ~/.agentnode/.env), offers to import it instead.
    Output shows only the masked tail and the honest storage label."""
    import getpass
    import os

    from agentnode_sdk.cli.auth import _masked, _storage_short, cmd_auth_test
    from agentnode_sdk.credential_store import LLM_PROVIDER_ENV, set_credential

    env_var = LLM_PROVIDER_ENV[provider]
    try:
        from agentnode_sdk.runtimes.agent_runner import _load_agentnode_env
        _load_agentnode_env()
    except Exception:
        pass
    env_val = (os.environ.get(env_var) or "").strip()

    token = ""
    if env_val:
        print(f"  Found {env_var} in your environment (env always overrides stored keys).")
        imp = _prompt(f"  Store this key from {env_var}? [Y/n]: ", "y")
        if imp.lower() != "n":
            token = env_val
    if not token:
        token = getpass.getpass(f"  Enter API key for {provider} (input hidden): ").strip()
    if not token:
        print("  No key entered — skipped.")
        return None

    storage = set_credential(provider, token, auth_type="api_key")
    if storage == "keyring":
        where = "OS keychain"
    else:
        where = "plaintext file (0600) — OS keychain unavailable on this system"
    print(f"  Credential stored for {provider} ({_masked(token)}) — storage: {where}.")

    t = _prompt("  Test the key now? (free endpoint, no completion call) [Y/n]: ", "y")
    if t.lower() != "n":
        try:
            rc = cmd_auth_test(provider)
        except Exception:
            rc = 3
        if rc not in (0, 1):
            # indeterminate/network — never block the wizard on it
            print(dim(f"  Could not verify right now — test later with "
                      f"`agentnode auth test {provider}`."))
    return _storage_short(storage)


def _credentials_screen() -> list[tuple[str, str]]:
    """Screen 5: LLM credentials (optional, default = skip). Returns the
    (provider, storage label) pairs stored in this run."""
    import sys

    print()
    print(bold("  LLM credentials (optional)"))
    print()
    print("  Agents and the sandboxed-agent LLM broker need a provider API key.")
    print("  Keys are stored in your OS keychain when available; otherwise in a")
    print("  plaintext file (0600) — AgentNode will tell you which.")
    print()

    if not sys.stdin.isatty():
        print(dim("  Non-interactive session — skipping. Add keys later with"))
        print(dim("  `agentnode auth set <provider>`."))
        return []

    stored: list[tuple[str, str]] = []
    while True:
        print("  [1] OpenAI   [2] Anthropic   [3] OpenRouter   [4] Skip for now")
        c = _choice("  Choice", ["1", "2", "3", "4"], "4")
        if c == "4":
            break
        provider = _LLM_CHOICES[c]
        if any(p == provider for p, _ in stored):
            print(f"  {provider} already added in this run.")
        else:
            label = _store_llm_key(provider)
            if label is not None:
                stored.append((provider, label))
        if len(stored) >= len(_LLM_CHOICES):
            break
        more = _prompt("  Add another provider? [y/N]: ", "n")
        if more.lower() != "y":
            break
    return stored


def _sandbox_screen(cfg: dict) -> tuple[str, bool]:
    """Screen 6: local sandbox (UX-3B).

    Diagnosis via the doctor's ``_build_env_checks`` (pure, read-only); the
    ONLY action is an optional, TTY-confirmed image pull via the existing
    ``cmd_sandbox_pull`` — the wizard itself never talks to docker. The enable
    prompt (default No) is offered ONLY when the sandbox is fully ready, and
    the flag rides the wizard's normal Save confirm (cancel persists nothing).

    Returns (status line for Summary/Success, image_still_missing)."""
    import sys

    from agentnode_sdk.cli.sandbox_commands import _build_env_checks, cmd_sandbox_pull

    print()
    print(bold("  Local sandbox"))
    print()
    print("  Community packages run in an isolated container sandbox.")
    print("  Trusted/curated packages run without it.")
    print()

    checks, ready, image_missing = _build_env_checks()

    def _render(check_list):
        for c in check_list:
            mark = "[OK]" if c["ok"] else ("[--]" if c["ok"] is None else "[!!]")
            print(f"  {mark} {c['check']}: {c['detail']}")
            if c["ok"] is False and c.get("fix"):
                print(dim(f"       -> {c['fix']}"))

    _render(checks)
    print()

    if image_missing and not ready:
        if sys.stdin.isatty():
            print("  Pull the pinned AgentNode sandbox image now? [y/N]")
            print(dim("  This downloads the digest-pinned sandbox image."))
            print(dim("  It does not install Docker/Podman."))
            print(dim("  It does not enable auto-pull."))
            answer = _prompt("  > ", "n")
            if answer.lower() == "y":
                rc = cmd_sandbox_pull()
                if rc == 0:
                    checks, ready, image_missing = _build_env_checks()
                else:
                    # never a wizard failure — the user can pull later
                    print(dim("  Pull failed or was skipped — the wizard continues."))
                    print(dim("  Run `agentnode sandbox pull` later."))
            else:
                print(dim("  Skipped. Run `agentnode sandbox pull` when ready."))
        else:
            print(dim("  Non-interactive session — run `agentnode sandbox doctor` later."))

    if ready:
        print()
        print("  Sandbox ready — community packages run isolated.")
        print()
        print("  Sandboxed community agents are currently disabled (default).")
        print("  Enabling lets verified/unverified community agents run isolated;")
        print("  without it they are refused.")
        if sys.stdin.isatty():
            en = _prompt("  Enable sandboxed community agents now? [y/N]: ", "n")
            if en.lower() == "y":
                cfg["agent_sandbox"]["enabled"] = True
            else:
                print(dim("  You can enable it later with "
                          "`agentnode config set agent_sandbox.enabled true`."))
        else:
            print(dim("  Enable later with "
                      "`agentnode config set agent_sandbox.enabled true`."))
        return "ready", False

    from agentnode_sdk.cli.sandbox_commands import _first_failure
    fail = _first_failure(checks)
    detail = (fail or {}).get("detail", "not ready")
    print(dim("  Not ready — details and guidance: agentnode sandbox doctor"))
    return f"not ready — {detail}", image_missing


def _wizard_flow() -> dict | None:
    cfg = default_config()

    # Screen 1: Intro
    print()
    print(section("AgentNode Setup"))
    print("  Configure how AgentNode manages capabilities for your agents.")
    print("  You can change these settings later with `agentnode setup`.")
    print()
    print(dim("  Press Enter to continue..."))
    _prompt("")

    # Screen 2: Installation behavior
    print()
    print(bold("  Installation behavior"))
    print()
    print("  [1] Automatic — install verified capabilities without asking")
    print("  [2] Review before install — ask before each installation")
    print("  [3] Manual only — never install automatically")
    print()
    choice = _choice("  Choice", ["1", "2", "3"], "1")
    if choice == "1":
        cfg["auto_upgrade_policy"] = "safe"
        cfg["install_confirmation"] = "auto"
    elif choice == "2":
        cfg["auto_upgrade_policy"] = "safe"
        cfg["install_confirmation"] = "prompt"
    else:
        cfg["auto_upgrade_policy"] = "off"
        cfg["install_confirmation"] = "auto"

    # Screen 3: Permission defaults
    print()
    print(bold("  Permission defaults"))
    print()
    for perm_label, perm_key in [
        ("Network", "network"),
        ("Filesystem", "filesystem"),
        ("Code execution", "code_execution"),
    ]:
        if perm_key == "code_execution":
            print(f"  {perm_label}: [1] sandboxed  [2] prompt  [3] deny")
            c = _choice("  Choice", ["1", "2", "3"], "1")
            cfg["permissions"][perm_key] = {"1": "sandboxed", "2": "prompt", "3": "deny"}[c]
        else:
            print(f"  {perm_label}: [1] allow  [2] prompt  [3] deny")
            c = _choice("  Choice", ["1", "2", "3"], "2")
            cfg["permissions"][perm_key] = {"1": "allow", "2": "prompt", "3": "deny"}[c]

    # Screen 4: Advanced (optional)
    print()
    print(bold("  Advanced settings"))
    print()
    adv = _prompt("  Configure trust level? [y/N]: ", "n")
    if adv.lower() == "y":
        print()
        print("  Minimum trust level:")
        print("  [1] verified — community-reviewed packages")
        print("  [2] trusted — manually approved by AgentNode team")
        print("  [3] curated — official AgentNode packages only")
        print()
        c = _choice("  Choice", ["1", "2", "3"], "1")
        cfg["trust"]["minimum_trust_level"] = {"1": "verified", "2": "trusted", "3": "curated"}[c]

    # Screen 5: LLM credentials (optional — UX-3A)
    stored_providers = _credentials_screen()

    # Screen 6: Local sandbox (UX-3B)
    sandbox_status, image_missing = _sandbox_screen(cfg)

    # Screen 7: Summary
    print()
    print(section("Summary"))
    print(kv("Installation behavior", installation_behavior_label(cfg)))
    print(kv("Trust level", cfg["trust"]["minimum_trust_level"]))
    if stored_providers:
        creds_line = ", ".join(f"{p} ({label})" for p, label in stored_providers)
    else:
        creds_line = "none — add later with `agentnode auth set <provider>`"
    print(kv("LLM credentials", creds_line))
    print(kv("Sandbox", sandbox_status))
    print(kv("Sandboxed community agents",
             "enabled" if cfg["agent_sandbox"]["enabled"] else "disabled"))
    print()
    print("  Permissions")
    print("  " + "-" * 11)
    print(kv("Network", cfg["permissions"]["network"]))
    print(kv("Filesystem", cfg["permissions"]["filesystem"]))
    print(kv("Code execution", cfg["permissions"]["code_execution"]))
    print()
    confirm = _prompt("  Save? [Y/n]: ", "y")
    if confirm.lower() == "n":
        print("\n  Setup cancelled. No changes saved.")
        return None

    save_config(cfg)

    # Screen 8: Success
    print()
    print(bold("  Configuration saved."))
    print()
    print(kv("Sandbox", sandbox_status))
    print(dim("  Community packages need a local sandbox. Trusted/curated"))
    print(dim("  packages can run without it."))
    print()
    print(dim("  Next steps:"))
    print(dim("    agentnode auth status                    check your credentials"))
    print(dim("    agentnode sandbox doctor                 check the sandbox runtime"))
    if image_missing:
        print(dim("    agentnode sandbox pull                   fetch the sandbox image"))
    if not cfg["agent_sandbox"]["enabled"]:
        print(dim("    agentnode config set agent_sandbox.enabled true"))
        print(dim("                                             enable sandboxed community agents"))
    print(dim("    agentnode install word-counter-pack     install a first capability"))
    print(dim("    agentnode run word-counter-pack --input '{\"text\":\"hello\"}'"))
    print(dim("    agentnode doctor                         check your whole setup"))
    print()

    return cfg
