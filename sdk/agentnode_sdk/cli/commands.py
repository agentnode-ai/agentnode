"""AgentNode CLI commands."""
from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

from agentnode_sdk.config import (
    config_exists,
    config_path,
    delete_config,
    get_value,
    installation_behavior_label,
    load_config,
    save_config,
    set_value,
)
from agentnode_sdk.cli.output import bold, dim, kv, section
from agentnode_sdk.installer import _lockfile_path, read_lockfile


def cmd_dashboard() -> int:
    """Show dashboard or run setup if first time."""
    if not config_exists():
        from agentnode_sdk.cli.setup_wizard import run_wizard

        return run_wizard()

    cfg = load_config()
    lock = read_lockfile()
    pkg_count = len(lock.get("packages", {}))

    print()
    print(section("AgentNode Settings"))
    print(kv("Installation behavior", installation_behavior_label(cfg)))
    print(kv("Trust level", cfg.get("trust", {}).get("minimum_trust_level", "verified")))
    print()
    print("  Permissions")
    print("  " + "-" * 11)
    perms = cfg.get("permissions", {})
    print(kv("Network", perms.get("network", "prompt")))
    print(kv("Filesystem", perms.get("filesystem", "prompt")))
    print(kv("Code execution", perms.get("code_execution", "sandboxed")))
    print()
    print(kv("Installed capabilities", str(pkg_count)))
    print()
    print(kv("Config", str(config_path())))
    print()
    print(dim("  Run `agentnode search <query>` to discover capabilities."))
    print(dim("  Run `agentnode setup` to change your settings."))
    print()
    return 0


def cmd_setup() -> int:
    from agentnode_sdk.cli.setup_wizard import run_wizard

    return run_wizard()


def cmd_doctor() -> int:
    import agentnode_sdk

    cfg = load_config()
    lock = read_lockfile()
    lockfile = _lockfile_path()
    pkg_count = len(lock.get("packages", {}))

    cfg_found = config_exists()
    cfg_valid = True
    if cfg_found:
        try:
            p = config_path()
            json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            cfg_valid = False

    py_version = platform.python_version()
    sdk_version = agentnode_sdk.__version__

    registry_status = "yes"
    try:
        import httpx

        resp = httpx.get("https://api.agentnode.net/v1/health", timeout=5)
        if resp.status_code != 200:
            registry_status = f"no (HTTP {resp.status_code})"
    except Exception:
        registry_status = "no (network unavailable)"

    if lockfile.is_file():
        lock_info = f"{lockfile} ({pkg_count} packages)"
    else:
        lock_info = "not found"

    print()
    print(section("AgentNode Doctor"))
    print(kv("Config file", "found" if cfg_found else "not found"))
    print(kv("Config valid", "yes" if cfg_valid else "no"))
    print(kv("SDK version", sdk_version))
    print(kv("Python version", py_version))
    print(kv("Config path", str(config_path())))
    print(kv("Lockfile", lock_info))
    print(kv("Registry reachable", registry_status))
    print()
    return 0


def cmd_reset() -> int:
    print()
    print("  This will delete your AgentNode configuration.")
    print("  Installed capabilities will not be removed.")
    print()
    try:
        confirm = input("  Reset configuration? [y/N]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n  Cancelled.")
        return 130

    if confirm.lower() != "y":
        print("  Cancelled.")
        return 0

    delete_config()
    print()
    print("  Configuration reset. Run `agentnode` to set up again.")
    print()
    return 0


def cmd_config() -> int:
    cfg = load_config()
    print()
    print(section("AgentNode Config"))
    print(kv("auto_upgrade_policy", cfg.get("auto_upgrade_policy", "safe")))
    print(kv("install_confirmation", cfg.get("install_confirmation", "auto")))
    print()
    trust = cfg.get("trust", {})
    print(kv("trust.minimum_trust_level", str(trust.get("minimum_trust_level", "verified"))))
    print(kv("trust.allow_unverified", str(trust.get("allow_unverified", False)).lower()))
    print()
    perms = cfg.get("permissions", {})
    print(kv("permissions.network", perms.get("network", "prompt")))
    print(kv("permissions.filesystem", perms.get("filesystem", "prompt")))
    print(kv("permissions.code_execution", perms.get("code_execution", "sandboxed")))
    print()
    print(dim(f"  Config file: {config_path()}"))
    print()
    return 0


def cmd_config_get(key: str) -> int:
    cfg = load_config()
    try:
        value = get_value(cfg, key)
        print(value)
        return 0
    except KeyError as e:
        print(str(e), file=sys.stderr)
        return 1


def cmd_config_set(key: str, value: str) -> int:
    cfg = load_config()
    try:
        cfg = set_value(cfg, key, value)
        save_config(cfg)
        print(f"  {key} = {value}")
        return 0
    except (KeyError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1


def cmd_search(query: str) -> int:
    try:
        from agentnode_sdk.client import AgentNodeClient

        client = AgentNodeClient()
        try:
            result = client.search(query=query)
        finally:
            client.close()

        if not result.hits:
            print()
            print(f"  No results for '{query}'.")
            print()
            return 0

        print()
        print(section(f"Search: {query}"))
        print(f"  {result.total} results\n")
        for hit in result.hits:
            trust = hit.trust_level or "unverified"
            version = hit.latest_version or ""
            print(f"  {bold(hit.slug)}")
            print(f"    {hit.summary}")
            parts: list[str] = []
            if version:
                parts.append(version)
            parts.append(trust)
            if hit.download_count:
                parts.append(f"{hit.download_count} downloads")
            print(f"    {dim(' | '.join(parts))}")
            print()

        print(dim("  Run `agentnode install <name>` to install a capability."))
        print()
        return 0
    except Exception as e:
        print(f"Search failed: {e}", file=sys.stderr)
        return 1


def cmd_install(capability: str, version: str | None = None, yes: bool = False) -> int:
    cfg = load_config()

    if cfg.get("install_confirmation") == "prompt" and not yes:
        try:
            confirm = input(f"  Install {capability}? [Y/n]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n  Cancelled.")
            return 130
        if confirm.lower() == "n":
            print("  Cancelled.")
            return 0

    try:
        from agentnode_sdk.client import AgentNodeClient

        client = AgentNodeClient()
        try:
            trust_min = cfg.get("trust", {}).get("minimum_trust_level", "verified")
            result = client.install(
                capability,
                version=version,
                require_verified=trust_min in ("verified", "trusted", "curated"),
                require_trusted=trust_min in ("trusted", "curated"),
            )
        finally:
            client.close()

        if result.installed:
            if result.already_installed:
                print(f"\n  {result.slug}@{result.version} is already installed.\n")
            else:
                print(f"\n  Installed {result.slug}@{result.version}.\n")
        else:
            print(f"\n  {result.message}\n")
            return 1

        return 0
    except Exception as e:
        print(f"Install failed: {e}", file=sys.stderr)
        return 1


def cmd_run(
    capability: str,
    input_data: str | None = None,
    file_path: str | None = None,
    raw: bool = False,
) -> int:
    if input_data and file_path:
        print("--input and --file are mutually exclusive.", file=sys.stderr)
        return 1

    if not input_data and not file_path:
        print()
        print("  No input provided.")
        print()
        print("  Use one of:")
        print("    agentnode run <capability> --input '{\"key\":\"value\"}'")
        print("    agentnode run <capability> --file input.json")
        print()
        return 1

    try:
        if file_path:
            data = json.loads(Path(file_path).read_text(encoding="utf-8"))
        else:
            data = json.loads(input_data)  # type: ignore[arg-type]
    except json.JSONDecodeError:
        print("Invalid JSON input.", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"File not found: {file_path}", file=sys.stderr)
        return 1

    if not isinstance(data, dict):
        print("Input must be a JSON object.", file=sys.stderr)
        return 1

    try:
        from agentnode_sdk.runner import run_tool

        result = run_tool(capability, **data)

        output = result.result if hasattr(result, "result") else result
        if raw:
            print(json.dumps(output, default=str))
        else:
            print()
            if isinstance(output, dict):
                for k, v in output.items():
                    print(kv(k, str(v)))
            else:
                print(f"  {output}")
            print()
        return 0
    except Exception as e:
        print(f"Run failed: {e}", file=sys.stderr)
        return 1


def cmd_remove(capability: str, yes: bool = False) -> int:
    lock = read_lockfile()
    pkgs = lock.get("packages", {})

    if capability not in pkgs:
        print(f"\n  {capability} is not installed.\n")
        return 1

    if not yes:
        try:
            confirm = input(f"  Remove {capability}? [y/N]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n  Cancelled.")
            return 130
        if confirm.lower() != "y":
            print("  Cancelled.")
            return 0

    del pkgs[capability]
    lock_path = _lockfile_path()
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

    print(f"\n  Removed {capability} from lockfile.\n")
    return 0


def cmd_capabilities() -> int:
    lock = read_lockfile()
    pkgs = lock.get("packages", {})

    if not pkgs:
        print()
        print("  No capabilities installed.")
        print()
        print(dim("  Run `agentnode search <query>` to find capabilities."))
        print()
        return 0

    print()
    print(section("Installed Capabilities"))
    for slug, info in pkgs.items():
        version = info.get("version", "?")
        trust = info.get("trust_level") or "unknown"
        print(f"  {bold(slug)} {dim(version)}  {dim(trust)}")
    print()
    print(dim(f"  {len(pkgs)} installed"))
    print()
    return 0


def cmd_capabilities_show(name: str) -> int:
    lock = read_lockfile()
    pkgs = lock.get("packages", {})

    if name not in pkgs:
        print(f"\n  {name} is not installed.\n")
        return 1

    pkg = pkgs[name]

    print()
    print(section(name))
    print(kv("Version", pkg.get("version", "?")))
    print(kv("Trust level", pkg.get("trust_level") or "unknown"))
    print(kv("Package type", pkg.get("package_type", "?")))
    print(kv("Runtime", pkg.get("runtime", "?")))
    print(kv("Entrypoint", pkg.get("entrypoint", "-")))
    print(kv("Installed at", pkg.get("installed_at", "?")))

    perms = pkg.get("permissions")
    if perms and isinstance(perms, dict):
        print()
        print("  Permissions")
        print("  " + "-" * 11)
        for k, v in perms.items():
            label = k.replace("_level", "").replace("_", " ").title()
            print(kv(label, str(v)))

    caps = pkg.get("capability_ids", [])
    if caps:
        print()
        print("  Capabilities")
        print("  " + "-" * 12)
        for c in caps:
            print(f"    {c}")

    print()
    return 0


def cmd_validate(path_str: str) -> int:
    """Validate a package directory before publishing."""
    from agentnode_sdk.cli.validate import validate_package_dir

    pkg_path = Path(path_str).resolve()
    if not pkg_path.is_dir():
        print(f"  Error: '{path_str}' is not a directory")
        return 1

    result = validate_package_dir(pkg_path)

    print()
    header = "AgentNode Package Validation"
    print(section(header))

    if result.package_id:
        label = f"{result.package_id}@{result.version}" if result.version else result.package_id
        print(kv("Package", label))
    if result.package_type:
        print(kv("Type", result.package_type))
    print()

    print(bold("  Checks"))
    print("  " + "-" * 6)
    for check in result.checks:
        status = "\033[32m[PASS]\033[0m" if check.passed else "\033[31m[FAIL]\033[0m"
        line = f"  {status} {check.label}"
        if check.detail:
            line += f" — {check.detail}"
        print(line)
    print()

    print(bold("  Tier Preview"))
    print("  " + "-" * 12)
    print(kv("Max tier", result.max_tier.capitalize()))
    print(kv("Verification mode", result.verification_mode))
    print(kv("Cases", str(result.cases_count)))
    gold_eligible = "yes" if result.max_tier == "gold" else "no"
    print(kv("Gold eligible", gold_eligible))
    print()

    if result.missing_items:
        print(bold("  Missing for Gold"))
        print("  " + "-" * 16)
        for item in result.missing_items:
            print(f"  • {item}")
        print()

    if result.has_errors:
        print(dim("  Fix the errors above before publishing."))
    elif result.max_tier == "gold":
        print(dim("  This package is Gold-eligible. Actual tier depends on verification after publish."))
    else:
        print(dim("  Add verification.cases to become Gold-eligible. See: agentnode.net/docs/publishing"))
    print()

    return 1 if result.has_errors else 0
