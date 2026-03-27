"""AgentNode setup wizard — 6-screen interactive configuration."""
from __future__ import annotations

from agentnode_sdk.config import (
    default_config,
    installation_behavior_label,
    save_config,
)
from agentnode_sdk.cli.output import bold, dim, kv, section


def run_wizard() -> int:
    """Run the 6-screen setup wizard. Returns exit code."""
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

    # Screen 5: Summary
    print()
    print(section("Summary"))
    print(kv("Installation behavior", installation_behavior_label(cfg)))
    print(kv("Trust level", cfg["trust"]["minimum_trust_level"]))
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

    # Screen 6: Success
    print()
    print(bold("  Configuration saved."))
    print()
    print(dim("  Next steps:"))
    print(dim("    agentnode search <query>    discover capabilities"))
    print(dim("    agentnode doctor            check your setup"))
    print(dim("    agentnode config            view your settings"))
    print()

    return cfg
