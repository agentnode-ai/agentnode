"""AgentNode setup wizard — interactive configuration.

Covers the full first-class config surface with multiple-choice prompts; the
recommended option is marked "(recommended)" and is the default (Enter / non-TTY).
Accepting every recommendation reproduces ``default_config()`` exactly, so the
wizard is non-breaking by construction.

Deeply nested config (``llm.providers``, the ``agent_sandbox.llm`` ceiling, and
``guard.tool_overrides`` / ``agent_overrides`` / ``rate_limits``) stays CLI/manual
only BY DESIGN and is surfaced only as follow-up hints — the wizard is a
first-class-settings tool, not a nested YAML editor.

UX invariants preserved:
- Config is built by mutating ``default_config()`` + one ``save_config(cfg)`` — not
  per-key config setters (booleans persist as real bools).
- No docker calls here; the only sandbox action is the reused ``cmd_sandbox_pull``.
- Credentials via getpass/env-import only, masked output, honest storage labels.
- Non-TTY never hangs: every prompt falls back to its recommended/default. An
  invalid *interactive* choice re-prompts — a typo must not set a security choice.
"""
from __future__ import annotations

import sys

from agentnode_sdk.config import (
    default_config,
    installation_behavior_label,
    save_config,
)
from agentnode_sdk.cli.output import bold, dim, kv, section

# Key-provider menu (grouped for readability); choice 8 = ollama (keyless,
# handled separately — never a key prompt), choice 9 = skip (the default).
_LLM_CHOICES = {
    "1": "openai", "2": "anthropic", "3": "openrouter",
    "4": "deepseek", "5": "mistral", "6": "qwen", "7": "gemini",
}
_OLLAMA_CHOICE = "8"
_SKIP_CHOICE = "9"

# Guard action types in a risk-ordered display order + friendly labels.
_GUARD_ORDER = [
    "delete", "write_external", "execute", "credential_use",
    "network_egress", "write_local", "read", "compute", "unknown",
]
_GUARD_LABELS = {
    "delete": "Delete resources",
    "write_external": "Write / send externally",
    "execute": "Execute code",
    "credential_use": "Use credentials",
    "network_egress": "Network egress",
    "write_local": "Write locally",
    "read": "Read data",
    "compute": "Compute",
    "unknown": "Unknown action type",
}


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


def _pick(title: str, options: list[tuple[str, object]], recommended: int,
          notes: list[str] | None = None) -> object:
    """Multiple-choice menu. ``options`` = ``[(label, value), ...]`` (1-indexed);
    ``recommended`` is the 1-based index of the recommended option (also the
    Enter/non-TTY default). ``notes`` (optional, same length) renders each option
    on its own line with a dim hint.

    Empty input → recommended. Non-TTY → recommended (never hangs). An INVALID
    interactive choice re-prompts (``Please choose 1–N.``) — it never silently
    falls back to the recommended value, so a typo cannot set a security choice.
    """
    n = len(options)
    print(f"  {title}")
    if notes:
        width = max(len(label) for label, _ in options) + 18
        for i, (label, _v) in enumerate(options, 1):
            tag = " (recommended)" if i == recommended else ""
            left = f"  [{i}] {label}{tag}"
            print(f"{left:<{width}}{dim(notes[i - 1])}")
    else:
        cells = []
        for i, (label, _v) in enumerate(options, 1):
            tag = " (recommended)" if i == recommended else ""
            cells.append(f"[{i}] {label}{tag}")
        print("  " + "   ".join(cells))

    if not sys.stdin.isatty():
        return options[recommended - 1][1]
    while True:
        raw = _prompt(f"  Choice [{recommended}]: ")
        if not raw:
            return options[recommended - 1][1]
        if raw.isdigit() and 1 <= int(raw) <= n:
            return options[int(raw) - 1][1]
        print(f"  Please choose 1–{n}.")


def _store_llm_key(provider: str) -> str | None:
    """Store one provider key via the vault. Returns the short storage label
    ("OS keychain"/"plaintext file") on success, None when skipped.

    Key entry is getpass-only (hidden, never argv); if the provider's env var
    is already set (incl. ~/.agentnode/.env), offers to import it instead.
    Output shows only the masked tail and the honest storage label."""
    import getpass
    import os

    from agentnode_sdk.cli.auth import _masked, _storage_short, cmd_auth_test
    from agentnode_sdk.credential_store import set_credential

    from agentnode_sdk.llm_providers import resolve_provider_spec
    env_var = (resolve_provider_spec(provider) or {}).get("api_key_env") or ""
    try:
        from agentnode_sdk.runtimes.agent_runner import _load_agentnode_env
        _load_agentnode_env()
    except Exception:
        pass
    env_val = (os.environ.get(env_var) or "").strip() if env_var else ""

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


def _credentials_screen(cfg: dict) -> list[tuple[str, str]]:
    """Screen: LLM credentials (optional, default = skip). Key providers come
    from the registry-backed menu; ollama is a keyless CONFIG selection. After a
    key provider is stored, offers to make it the default provider (mirrors the
    ollama path). Returns the (provider, storage label) pairs selected."""
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
    all_options = list(_LLM_CHOICES) + [_OLLAMA_CHOICE, _SKIP_CHOICE]
    while True:
        print("  Recommended:  [1] OpenAI    [2] Anthropic   [3] OpenRouter")
        print("  More:         [4] DeepSeek  [5] Mistral     [6] Qwen      [7] Gemini")
        print("  Local:        [8] Ollama — keyless, requires a running Ollama")
        print("  [9] Skip for now (recommended)")
        c = _prompt("  Choice [9]: ", _SKIP_CHOICE)
        if c not in all_options:
            print("  Please choose 1–9.")
            continue
        if c == _SKIP_CHOICE:
            break
        if c == _OLLAMA_CHOICE:
            # keyless local provider: a config selection, NOT a credential.
            if any(p == "ollama" for p, _ in stored):
                print("  ollama already selected in this run.")
            else:
                print("  Ollama runs models locally — no API key, no per-token cost.")
                print(dim("  Requires a running Ollama (https://ollama.com); the wizard"))
                print(dim("  does not start or probe it."))
                use = _prompt("  Use Ollama as your default LLM provider? [Y/n]: ", "y")
                if use.lower() != "n":
                    cfg["llm"]["default_provider"] = "ollama"
                    stored.append(("ollama", "local, keyless"))
                    print(dim("  Selected. Test later with `agentnode auth test ollama`."))
        else:
            provider = _LLM_CHOICES[c]
            if any(p == provider for p, _ in stored):
                print(f"  {provider} already added in this run.")
            else:
                label = _store_llm_key(provider)
                if label is not None:
                    stored.append((provider, label))
        if len(stored) >= len(_LLM_CHOICES) + 1:
            break
        more = _prompt("  Add another provider? [y/N]: ", "n")
        if more.lower() != "y":
            break

    # Offer to make a stored KEY provider the default (ollama already sets it above).
    key_providers = [p for p, _ in stored if p != "ollama"]
    if key_providers and cfg["llm"]["default_provider"] not in key_providers:
        first = key_providers[0]
        ans = _prompt(f"  Use {first} as your default LLM provider? [Y/n]: ", "y")
        if ans.lower() != "n":
            cfg["llm"]["default_provider"] = first
    return stored


def _guard_screen(cfg: dict) -> str:
    """Screen: Guard posture. Sets the 9 ``guard.<action>`` keys from a preset
    bundle, or drills into all 9 individually on "Customize each". Reuses
    guard.py's shipped Balanced (== defaults) and Strict bundles. Returns the
    chosen posture label for the Summary."""
    from agentnode_sdk.guard import _DEFAULT_GUARD_POLICY, _STRICT_GUARD_POLICY

    print()
    print(bold("  Guard posture"))
    print()
    print("  How tools are gated at run time (the pre-execution policy).")
    posture = _pick("Choose a posture", [
        ("Balanced", "balanced"),
        ("Strict", "strict"),
        ("Permissive", "permissive"),
        ("Customize each", "custom"),
    ], recommended=1, notes=[
        "risky actions ask, safe ones allow",
        "destructive actions denied, more prompts",
        "allow everything, ask only on unknown",
        "set all 9 action types yourself",
    ])

    if posture == "balanced":
        pass  # cfg["guard"] is already the shipped default == Balanced
    elif posture == "strict":
        cfg["guard"].update(_STRICT_GUARD_POLICY)
    elif posture == "permissive":
        cfg["guard"].update({a: "allow" for a in _GUARD_ORDER})
        cfg["guard"]["unknown"] = "prompt"
    else:  # custom — recommended per action = the Balanced value
        print()
        print(dim("  Set each action type (allow / prompt / deny):"))
        pol = ("allow", "allow"), ("prompt", "prompt"), ("deny", "deny")
        idx = {"allow": 1, "prompt": 2, "deny": 3}
        for action in _GUARD_ORDER:
            rec = idx[_DEFAULT_GUARD_POLICY[action]]
            cfg["guard"][action] = _pick(_GUARD_LABELS[action], list(pol), recommended=rec)
    return posture


def _sandbox_screen(cfg: dict) -> tuple[str, bool]:
    """Screen: host-trust policy + local sandbox.

    Sets ``sandbox.host_trust_policy`` (which trust tiers may run on the host),
    then diagnoses the container runtime via the doctor's read-only
    ``_build_env_checks``. The ONLY action is an optional, TTY-confirmed image
    pull via the existing ``cmd_sandbox_pull`` — the wizard never talks to docker.
    The agent-sandbox enable prompt (default No) is offered only when the sandbox
    is fully ready. Returns (status line for Summary/Success, image_still_missing).
    """
    from agentnode_sdk.cli.sandbox_commands import _build_env_checks, cmd_sandbox_pull

    print()
    print(bold("  Sandbox & host-trust policy"))
    print()
    print("  Community packages always run in an isolated container sandbox.")
    print("  By default, trusted third-party packages are sandboxed too — only")
    print("  AgentNode's own curated packages run directly on your host.")
    print()

    htp = _pick("Which trust tiers may run directly on your host?", [
        ("Default", "default"),
        ("Curated only", "curated_only"),
        ("None", "none"),
    ], recommended=2, notes=[
        "curated + trusted on host (more permissive than the shipped default)",
        "trusted is sandboxed; only curated on host (shipped default)",
        "everything sandboxed; nothing on the host",
    ])
    cfg["sandbox"]["host_trust_policy"] = htp
    if htp != "default":
        print(dim("  Note: stronger isolation can break trusted/curated packages that"))
        print(dim("  expect host FS, broad tools, LLM keys or network — a reinstall may"))
        print(dim("  be needed. Check with `agentnode sandbox doctor <slug>`."))
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
        print("  Sandboxed community agents are enabled by default: verified/")
        print("  unverified community agents run isolated in the sandbox, or are")
        print("  refused if it is unavailable — never on the host.")
        if sys.stdin.isatty():
            dis = _prompt("  Disable sandboxed community agents? [y/N]: ", "n")
            if dis.lower() == "y":
                cfg["agent_sandbox"]["enabled"] = False
                print(dim("  Disabled — community agents will be refused outright. "
                          "Re-enable with `agentnode config set agent_sandbox.enabled true`."))
        return "ready", False

    from agentnode_sdk.cli.sandbox_commands import _first_failure
    fail = _first_failure(checks)
    detail = (fail or {}).get("detail", "not ready")
    print(dim("  Not ready — details and guidance: agentnode sandbox doctor"))
    return f"not ready — {detail}", image_missing


def _advanced_screen(cfg: dict) -> None:
    """Screen: advanced settings (opt-in gate, default No, non-TTY skip). Covers
    the two niche first-class keys; deeper nested config stays CLI-only."""
    print()
    print(bold("  Advanced settings (optional)"))
    print()
    if not sys.stdin.isatty():
        return
    if _prompt("  Configure advanced settings? [y/N]: ", "n").lower() != "y":
        return

    cfg["credentials"]["require_before_auto_install"] = _pick(
        "During auto-install, skip packages needing credentials you don't have?",
        [("Yes", True), ("No", False)], recommended=1,
        notes=["skip them (recommended)", "try anyway"])
    cfg["risk_policies"]["external_write_capable"] = _pick(
        "Packages that can write or send data externally",
        [("log", "log"), ("allow", "allow"), ("prompt", "prompt"), ("deny", "deny")],
        recommended=1)


def _wizard_flow() -> dict | None:
    cfg = default_config()

    # Screen 1: Intro
    print()
    print(section("AgentNode Setup"))
    print("  Configure how AgentNode manages capabilities for your agents.")
    print("  Each choice marks our (recommended) default — press Enter to accept it.")
    print("  You can change everything later with `agentnode setup` or `config set`.")
    print()
    print(dim("  Press Enter to continue..."))
    _prompt("")

    # Screen 2: Installation behavior (one choice -> two keys)
    print()
    print(bold("  Installation behavior"))
    print()
    behavior = _pick("How should capabilities be installed?", [
        ("Automatic", "auto"),
        ("Review before install", "review"),
        ("Manual only", "manual"),
    ], recommended=1, notes=[
        "install verified capabilities without asking",
        "ask before each installation",
        "never install automatically",
    ])
    if behavior == "auto":
        cfg["auto_upgrade_policy"] = "safe"
        cfg["install_confirmation"] = "auto"
    elif behavior == "review":
        cfg["auto_upgrade_policy"] = "safe"
        cfg["install_confirmation"] = "prompt"
    else:
        cfg["auto_upgrade_policy"] = "off"
        cfg["install_confirmation"] = "auto"

    # Screen 3: Trust level (now a direct screen)
    print()
    print(bold("  Minimum trust level"))
    print()
    cfg["trust"]["minimum_trust_level"] = _pick("Minimum trust tier to install/run", [
        ("verified", "verified"),
        ("trusted", "trusted"),
        ("curated", "curated"),
    ], recommended=1, notes=[
        "community-reviewed packages",
        "manually approved by the AgentNode team",
        "official AgentNode packages only",
    ])

    # Screen 4: Permission defaults
    print()
    print(bold("  Permission defaults"))
    print()
    cfg["permissions"]["network"] = _pick(
        "Network access", [("allow", "allow"), ("prompt", "prompt"), ("deny", "deny")],
        recommended=2)
    cfg["permissions"]["filesystem"] = _pick(
        "Filesystem access", [("allow", "allow"), ("prompt", "prompt"), ("deny", "deny")],
        recommended=2)
    cfg["permissions"]["code_execution"] = _pick(
        "Code execution",
        [("sandboxed", "sandboxed"), ("prompt", "prompt"), ("deny", "deny")],
        recommended=1)

    # Screen 5: Guard posture (sets the 9 guard.* keys)
    guard_posture = _guard_screen(cfg)

    # Screen 6: LLM credentials (optional)
    stored_providers = _credentials_screen(cfg)

    # Screen 7: Sandbox + host-trust policy
    sandbox_status, image_missing = _sandbox_screen(cfg)

    # Screen 8: Advanced (opt-in)
    _advanced_screen(cfg)

    # Screen 9: Summary
    print()
    print(section("Summary"))
    print(kv("Installation behavior", installation_behavior_label(cfg)))
    print(kv("Trust level", cfg["trust"]["minimum_trust_level"]))
    print(kv("Guard posture", guard_posture))
    if stored_providers:
        creds_line = ", ".join(f"{p} ({label})" for p, label in stored_providers)
    else:
        creds_line = "none — add later with `agentnode auth set <provider>`"
    print(kv("LLM credentials", creds_line))
    print(kv("LLM default provider", cfg["llm"]["default_provider"]))
    print(kv("Host-trust policy", cfg["sandbox"]["host_trust_policy"]))
    print(kv("Sandbox", sandbox_status))
    print(kv("Agent sandbox",
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

    # Screen 10: Success
    print()
    print(bold("  Configuration saved."))
    print()
    print(kv("Sandbox", sandbox_status))
    print(kv("Host-trust policy", cfg["sandbox"]["host_trust_policy"]))
    print(dim("  Community packages need a local sandbox. Trusted/curated packages"))
    print(dim("  run on the host unless the host-trust policy sandboxes them."))
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
    print(dim("  Fine-tuning beyond these settings (CLI):"))
    print(dim("    agentnode guard set <action> <policy>    per-action guard overrides"))
    print(dim("    agentnode config set sandbox.host_trust_policy <default|curated_only|none>"))
    print(dim("    agentnode config set llm.default_provider <provider>   (custom endpoints via config)"))
    print()

    return cfg
