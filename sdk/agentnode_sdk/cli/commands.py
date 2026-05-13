"""AgentNode CLI commands."""
from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

from agentnode_sdk.config import (
    CONFIG_DESCRIPTIONS,
    VALID_VALUES,
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
    print(dim("  Run `agentnode discover` to browse available packages."))
    print(dim("  Run `agentnode search <query>` for full-text search."))
    print(dim("  Run `agentnode recommend` for personalized suggestions."))
    print()
    return 0


def cmd_setup() -> int:
    from agentnode_sdk.cli.setup_wizard import run_wizard

    return run_wizard()


def cmd_doctor(fix: bool = False, json_output: bool = False) -> int:
    import agentnode_sdk

    cfg = load_config()
    lock = read_lockfile()
    lockfile = _lockfile_path()
    pkgs = lock.get("packages", {})
    pkg_count = len(pkgs)

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

    registry_ok = True
    registry_status = "yes"
    try:
        import httpx

        resp = httpx.get("https://api.agentnode.net/health", timeout=5)
        if resp.status_code != 200:
            registry_status = f"no (HTTP {resp.status_code})"
            registry_ok = False
    except Exception:
        registry_status = "no (network unavailable)"
        registry_ok = False

    if lockfile.is_file():
        lock_info = f"{lockfile} ({pkg_count} packages)"
    else:
        lock_info = "not found"

    health = {
        "config": {"ok": cfg_found and cfg_valid, "detail": "valid" if cfg_found and cfg_valid else "not found" if not cfg_found else "invalid"},
        "sdk": {"ok": True, "version": sdk_version},
        "python": {"ok": True, "version": py_version},
        "registry": {"ok": registry_ok, "detail": registry_status},
        "lockfile": {"ok": lockfile.is_file(), "packages": pkg_count},
    }

    if json_output:
        installed = {}
        for slug, info in pkgs.items():
            installed[slug] = {
                "trust_level": info.get("trust_level", "unknown"),
                "capability_ids": info.get("capability_ids", []),
            }
        report = {"health": health, "installed": installed}
        print(json.dumps(report, indent=2))
        return 0

    print()
    print(section("AgentNode Doctor"))

    # --- System Health ---
    print(bold("  System"))
    print("  " + "-" * 6)
    config_detail = health["config"]["detail"]
    _doctor_check("Config", cfg_found and cfg_valid, config_detail)
    _doctor_check("SDK", True, f"v{sdk_version}")
    _doctor_check("Python", True, py_version)
    _doctor_check("Registry", registry_ok, registry_status)
    _doctor_check("Lockfile", lockfile.is_file(), f"{pkg_count} packages" if lockfile.is_file() else "not found")
    print()

    # --- Installed Capabilities ---
    installed_caps: set[str] = set()
    installed_slugs: set[str] = set(pkgs.keys())
    for info in pkgs.values():
        for cap_id in info.get("capability_ids", []):
            installed_caps.add(cap_id)

    if pkgs:
        print(bold("  Installed"))
        print("  " + "-" * 9)
        for slug in sorted(installed_slugs):
            info = pkgs[slug]
            trust = info.get("trust_level") or "unknown"
            caps = info.get("capability_ids", [])
            cap_str = f"  ({', '.join(caps)})" if caps else ""
            print(f"    {slug} {dim(f'[{trust}]{cap_str}')}")
        print()

    # --- Gap Analysis ---
    if not registry_ok:
        print(dim("  Cannot analyze gaps — registry unreachable."))
        print()
        return 0

    # Determine missing complementary capabilities (weighted graph)
    from agentnode_sdk.capability_graph import missing_for, priority_label

    gaps = missing_for(installed_caps, min_weight=0.3)
    missing_caps = [g[0] for g in gaps]

    if not missing_caps and not installed_caps:
        print(bold("  Analysis"))
        print("  " + "-" * 8)
        print("    No packages installed. Get started:")
        print()
        print(dim("    agentnode discover           Browse available packages"))
        print(dim("    agentnode resolve <cap>      Find a specific capability"))
        print()
        return 0

    if not missing_caps:
        print(bold("  Analysis"))
        print("  " + "-" * 8)
        print("    \033[32m[OK]\033[0m No missing capabilities detected.")
        print()
        print(dim("  Run `agentnode discover --new` to see what's new."))
        print()
        return 0

    # Resolve missing capabilities to actual packages
    try:
        from agentnode_sdk.client import AgentNodeClient

        client = AgentNodeClient()
        try:
            result = client.resolve(missing_caps[:10], limit=15)
        finally:
            client.close()
    except Exception as e:
        print(f"  Gap analysis failed: {e}")
        print()
        return 1

    # Filter out already-installed
    suggestions = [
        pkg for pkg in result.results
        if pkg.slug not in installed_slugs
    ][:6]

    if not suggestions:
        print(bold("  Analysis"))
        print("  " + "-" * 8)
        print("    \033[32m[OK]\033[0m Setup looks complete.")
        print()
        return 0

    gap_lookup = {g[0]: (g[1], g[2]) for g in gaps}

    # Show gaps and suggestions
    print(bold("  Missing Capabilities"))
    print("  " + "-" * 20)
    for cap in missing_caps[:8]:
        score, reason = gap_lookup.get(cap, (0.0, ""))
        prio = priority_label(score)
        marker = "\033[31m[!!]\033[0m" if prio == "high" else "\033[33m[-]\033[0m"
        print(f"    {marker} {cap} {dim(f'(priority: {score:.1f})')}")
        if reason:
            print(f"        {dim('↳ ' + reason)}")
    print()

    print(bold("  Suggested Fixes"))
    print("  " + "-" * 15)
    install_slugs = []
    for i, pkg in enumerate(suggestions, 1):
        trust = pkg.trust_level or "unverified"
        matched = ", ".join(pkg.matched_capabilities) if pkg.matched_capabilities else ""
        print(f"    {i}. {bold(pkg.slug)} {dim(f'[{trust}]')}")
        if matched:
            print(f"       {dim(f'provides: {matched}')}")
        install_slugs.append(pkg.slug)
    print()

    if fix:
        # Respect auto_upgrade_policy
        if cfg.get("auto_upgrade_policy") == "off":
            print()
            print(dim("  Auto-install disabled (auto_upgrade_policy: off)."))
            print(dim("  Install manually:"))
            for slug in install_slugs:
                print(dim(f"    agentnode install {slug}"))
            print()
            return 0

        # Auto-install suggested packages
        print(bold("  Installing..."))
        print("  " + "-" * 12)
        from agentnode_sdk.client import AgentNodeClient as _FixClient

        trust_min = cfg.get("trust", {}).get("minimum_trust_level", "verified")
        fix_client = _FixClient()
        success_count = 0
        try:
            for slug in install_slugs:
                try:
                    r = fix_client.install(
                        slug,
                        require_verified=trust_min in ("verified", "trusted", "curated"),
                        require_trusted=trust_min in ("trusted", "curated"),
                    )
                    if r.installed:
                        print(f"    \033[32m[OK]\033[0m {slug} installed")
                        success_count += 1
                    else:
                        print(f"    \033[31m[!!]\033[0m {slug}: {r.message}")
                except Exception as e:
                    print(f"    \033[31m[!!]\033[0m {slug}: {e}")
        finally:
            fix_client.close()
        print()
        if success_count == len(install_slugs):
            print("  \033[32mSystem ready.\033[0m")
        else:
            print(f"  {success_count}/{len(install_slugs)} installed.")
        print()
    else:
        # Show install command
        cmd = "agentnode install " + " ".join(install_slugs[:4])
        print(dim("  Fix with:"))
        print(f"    {cmd}")
        print()
        print(dim("  Or auto-fix all: agentnode doctor --fix"))
        print()

    return 0


def _doctor_check(label: str, ok: bool, detail: str) -> None:
    status = "\033[32m[OK]\033[0m" if ok else "\033[31m[!!]\033[0m"
    print(f"    {status} {label}: {detail}")


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
    w = 42
    print()
    print(section("AgentNode Config"))
    print(kv("auto_upgrade_policy", cfg.get("auto_upgrade_policy", "safe"), w))
    print(kv("install_confirmation", cfg.get("install_confirmation", "auto"), w))
    print()
    trust = cfg.get("trust", {})
    print(kv("trust.minimum_trust_level", str(trust.get("minimum_trust_level", "verified")), w))
    print()
    perms = cfg.get("permissions", {})
    print(kv("permissions.network", perms.get("network", "prompt"), w))
    print(kv("permissions.filesystem", perms.get("filesystem", "prompt"), w))
    print(kv("permissions.code_execution", perms.get("code_execution", "sandboxed"), w))
    print()
    creds = cfg.get("credentials", {})
    print(kv("credentials.require_before_auto_install", str(creds.get("require_before_auto_install", True)).lower(), w))
    print()
    risk = cfg.get("risk_policies", {})
    for rk, rv in risk.items():
        print(kv(f"risk_policies.{rk}", str(rv), w))
    print()
    print(dim(f"  Config file: {config_path()}"))
    print(dim("  Run `agentnode config list` for descriptions and allowed values."))
    print()
    return 0


def cmd_config_list() -> int:
    cfg = load_config()
    print()
    print(section("AgentNode Config Reference"))
    print()
    for key, allowed in VALID_VALUES.items():
        try:
            current = str(get_value(cfg, key)).lower()
        except KeyError:
            current = "?"
        desc = CONFIG_DESCRIPTIONS.get(key, "")
        print(f"  {bold(key)}")
        print(f"    values:  {', '.join(allowed)}")
        print(f"    current: {current}")
        if desc:
            print(f"    {dim(desc)}")
        print()
    print(dim(f"  Config file: {config_path()}"))
    print(dim("  Set with: agentnode config set <key> <value>"))
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


def cmd_discover(
    category: str | None = None,
    trending: bool = False,
    new: bool = False,
    package_type: str | None = None,
) -> int:
    """Browse the registry: trending, new, or by category."""
    try:
        from agentnode_sdk.client import AgentNodeClient

        client = AgentNodeClient()
        try:
            # If specific view requested, show just that
            if trending:
                result = client.search(sort_by="download_count:desc", per_page=15)
                print()
                print(section("Trending"))
                _print_discover_hits(result.hits)
                return 0

            if new:
                result = client.search(sort_by="published_at:desc", per_page=15)
                print()
                print(section("Recently Published"))
                _print_discover_hits(result.hits)
                return 0

            if category:
                result = client.search(query=category, per_page=20)
                print()
                print(section(f"Category: {category}"))
                if not result.hits:
                    print(f"  No packages found for '{category}'.")
                else:
                    _print_discover_hits(result.hits)
                print()
                print(dim(f"  Run `agentnode search {category}` for full-text search."))
                print()
                return 0

            # Default: show overview (trending + new + categories)
            trending_result = client.search(sort_by="download_count:desc", per_page=8)
            new_result = client.search(sort_by="published_at:desc", per_page=5)
        finally:
            client.close()

        print()
        print(section("Discover"))
        print()

        # Trending
        print(bold("  Trending"))
        print("  " + "-" * 8)
        _print_discover_hits(trending_result.hits, numbered=True)

        # New
        print(bold("  Recently Published"))
        print("  " + "-" * 18)
        _print_discover_hits(new_result.hits, numbered=True)

        # Categories
        print(bold("  Browse by Category"))
        print("  " + "-" * 18)
        categories = ["connector", "research", "automation", "data", "character"]
        for cat in categories:
            print(f"    agentnode discover --category {cat}")
        print()

        # Navigation hints
        print(dim("  Commands:"))
        print(dim("    agentnode discover --trending       Top packages by installs"))
        print(dim("    agentnode discover --new            Recently published"))
        print(dim("    agentnode discover --category X     Browse by category"))
        print(dim("    agentnode search <query>            Full-text search"))
        print(dim("    agentnode resolve <capability>      Find by capability ID"))
        print()
        return 0
    except Exception as e:
        print(f"Discover failed: {e}", file=sys.stderr)
        return 1


def _print_discover_hits(hits: list, numbered: bool = False) -> None:
    """Print search hits in compact discover format."""
    for i, hit in enumerate(hits, 1):
        tier = ""
        trust = hit.trust_level or "unverified"
        if trust in ("trusted", "curated"):
            tier = f" [{trust}]"
        elif trust == "verified":
            tier = " [verified]"

        prefix = f"  {i:2}. " if numbered else "  "
        print(f"{prefix}{bold(hit.slug)}{dim(tier)}")
        print(f"{'     ' if numbered else '  '}  {dim(hit.summary or '')}")
    print()


def cmd_resolve(capabilities: list[str], framework: str | None = None, json_output: bool = False) -> int:
    """Resolve capability IDs to ranked package recommendations."""
    try:
        from agentnode_sdk.client import AgentNodeClient

        client = AgentNodeClient()
        try:
            result = client.resolve(capabilities, framework=framework)
        finally:
            client.close()

        # Re-rank with local context
        from agentnode_sdk.resolve import rerank
        lock = read_lockfile()
        _pkgs = lock.get("packages", {})
        _inst_caps: set[str] = set()
        for info in _pkgs.values():
            for c in info.get("capability_ids", []):
                _inst_caps.add(c)
        result.results = rerank(result.results, _inst_caps, set(_pkgs.keys()))

        if not result.results:
            if json_output:
                print(json.dumps({"query": capabilities, "total": 0, "results": []}))
                return 0
            print()
            print(f"  No packages found for: {', '.join(capabilities)}")
            print()
            print(dim("  Try `agentnode search <query>` for full-text search."))
            print()
            return 0

        if json_output:
            items = []
            for pkg in result.results[:10]:
                items.append({
                    "slug": pkg.slug,
                    "version": pkg.version,
                    "summary": pkg.summary,
                    "score": pkg.score,
                    "trust_level": pkg.trust_level or "unverified",
                    "matched_capabilities": pkg.matched_capabilities or [],
                })
            print(json.dumps({"query": capabilities, "total": result.total, "results": items}, indent=2))
            return 0

        cap_label = ", ".join(capabilities)
        print()
        print(section(f"Resolve: {cap_label}"))
        print(f"  {result.total} match{'es' if result.total != 1 else ''}\n")

        for i, pkg in enumerate(result.results[:10], 1):
            score_str = f"{pkg.score:.0f}"
            trust = pkg.trust_level or "unverified"
            matched = ", ".join(pkg.matched_capabilities) if pkg.matched_capabilities else ""

            print(f"  {i}. {bold(pkg.slug)} {dim(f'v{pkg.version}')}")
            print(f"     {pkg.summary}")
            parts = [f"score: {score_str}", trust]
            if matched:
                parts.append(f"caps: {matched}")
            print(f"     {dim(' | '.join(parts))}")
            print()

        best = result.results[0]
        print(dim(f"  Install best match:"))
        print(f"    agentnode install {best.slug}")
        print()
        return 0
    except Exception as e:
        print(f"Resolve failed: {e}", file=sys.stderr)
        return 1


def cmd_recommend(json_output: bool = False) -> int:
    """Recommend packages based on what's installed."""
    from agentnode_sdk.capability_graph import missing_for, priority_label

    lock = read_lockfile()
    pkgs = lock.get("packages", {})

    if not pkgs:
        if json_output:
            print(json.dumps({"installed_capabilities": [], "recommendations": []}))
            return 0
        print()
        print("  No packages installed yet.")
        print()
        print(dim("  Run `agentnode discover` to browse available packages."))
        print()
        return 0

    installed_slugs = set(pkgs.keys())
    installed_caps: set[str] = set()
    for info in pkgs.values():
        for cap_id in info.get("capability_ids", []):
            installed_caps.add(cap_id)

    gaps = missing_for(installed_caps, min_weight=0.3)
    if not gaps:
        if json_output:
            print(json.dumps({"installed_capabilities": sorted(installed_caps), "recommendations": []}))
            return 0
        print()
        print("  No additional recommendations based on your current setup.")
        print()
        print(dim("  Run `agentnode discover --trending` for popular packages."))
        print()
        return 0

    gap_caps = [g[0] for g in gaps[:8]]

    try:
        from agentnode_sdk.client import AgentNodeClient

        client = AgentNodeClient()
        try:
            result = client.resolve(gap_caps, limit=12)
        finally:
            client.close()
    except Exception as e:
        print(f"Recommend failed: {e}", file=sys.stderr)
        return 1

    recommendations = [pkg for pkg in result.results if pkg.slug not in installed_slugs]

    gap_lookup = {g[0]: (g[1], g[2]) for g in gaps}

    if json_output:
        items = []
        for pkg in recommendations[:8]:
            best_cap = ""
            best_score = 0.0
            best_reason = ""
            for mc in pkg.matched_capabilities:
                if mc in gap_lookup and gap_lookup[mc][0] > best_score:
                    best_score = gap_lookup[mc][0]
                    best_reason = gap_lookup[mc][1]
                    best_cap = mc
            items.append({
                "slug": pkg.slug,
                "capability": best_cap,
                "priority": priority_label(best_score),
                "score": best_score,
                "reason": best_reason,
                "trust_level": pkg.trust_level or "unverified",
                "summary": pkg.summary,
            })
        print(json.dumps({
            "installed_capabilities": sorted(installed_caps),
            "recommendations": items,
        }, indent=2))
        return 0

    if not recommendations:
        print()
        print("  Your setup looks complete — no additional recommendations.")
        print()
        return 0

    print()
    print(section("Recommendations"))
    print()
    print(dim("  Based on your installed packages:"))
    for slug in sorted(installed_slugs)[:5]:
        print(dim(f"    - {slug}"))
    if len(installed_slugs) > 5:
        print(dim(f"    ... +{len(installed_slugs) - 5} more"))
    print()

    current_priority = None
    for i, pkg in enumerate(recommendations[:8], 1):
        best_score = 0.0
        best_reason = ""
        for mc in pkg.matched_capabilities:
            if mc in gap_lookup and gap_lookup[mc][0] > best_score:
                best_score = gap_lookup[mc][0]
                best_reason = gap_lookup[mc][1]

        priority = priority_label(best_score)
        if priority != current_priority:
            current_priority = priority
            label = {"high": "Priority: High", "suggested": "Priority: Suggested", "low": "Other"}.get(priority, priority)
            print(f"  {bold(label)}")
            print("  " + "-" * len(label))

        trust = pkg.trust_level or "unverified"
        print(f"  {i}. {bold(pkg.slug)} {dim(f'[{trust}]')}")
        print(f"     {pkg.summary}")
        if best_reason:
            print(f"     {dim('↳ ' + best_reason)}")
        print()

    print(dim("  Install with: agentnode install <name>"))
    print()
    return 0


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
                print(f"\n  Installed {result.slug}@{result.version}.")
                _print_install_guard_summary(result.slug)
                print()
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
    explain: bool = False,
    dry_run: bool = False,
    json_output: bool = False,
) -> int:
    # Detect if this is a natural language task (contains spaces = not a slug)
    if " " in capability.strip():
        return _cmd_run_smart(capability, raw=raw, explain=explain, dry_run=dry_run, json_output=json_output)

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
        print("    agentnode run \"search for AI news\"  (smart mode)")
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
        from agentnode_sdk.guard import cli_confirmation_callback

        is_interactive = os.environ.get("AGENTNODE_NON_INTERACTIVE", "").lower() not in ("true", "1")
        callback = cli_confirmation_callback if is_interactive else None

        result = run_tool(capability, confirmation_callback=callback, **data)

        if result.mode_used == "policy_denied" and result.policy:
            _print_guard_block(capability, None, result)
            return 1

        if explain and hasattr(result, "policy") and result.policy:
            print()
            print(section("Explain"))
            print(kv("Package", capability))
            print(kv("Policy", result.policy["action"]))
            print(kv("Reason", result.policy["reason"]))
            print(kv("Source", result.policy["source"]))
            print(kv("Mode", result.mode_used))
            if result.duration_ms:
                print(kv("Duration", f"{result.duration_ms:.0f}ms"))
            print()

        if json_output:
            print(json.dumps(result.to_dict(), default=str))
            return 0 if result.success else 1

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


def _print_guard_block(slug: str, tool_name: str | None, result) -> None:
    """Print structured guard deny/reject output for CLI users."""
    from agentnode_sdk.guard import GuardDecision, format_guard_deny
    policy = result.policy or {}
    action_types = policy.get("guard_action_types", [])
    risk_level = policy.get("guard_risk_level", "unknown")
    decision = GuardDecision(
        action="deny",
        reason=policy.get("reason", result.error or ""),
        action_types=action_types,
        risk_level=risk_level,
        source=policy.get("source", "guard"),
    )
    print(format_guard_deny(slug, tool_name, decision), file=sys.stderr)


def _cmd_run_smart(
    task: str, raw: bool = False, explain: bool = False,
    dry_run: bool = False, json_output: bool = False,
) -> int:
    """Smart run: parse natural language task → resolve → install → execute."""
    from agentnode_sdk.planner import _split_task

    parts = _split_task(task)
    if len(parts) > 1:
        return _cmd_run_plan(task, raw=raw, explain=explain, dry_run=dry_run, json_output=json_output)

    from agentnode_sdk.cli.smart_run import parse_task, get_alternatives
    from agentnode_sdk.installer import read_lockfile as _read_lock

    parsed = parse_task(task)
    if not parsed:
        print()
        print(f"  Could not understand task: \"{task}\"")
        print()
        print(dim("  Try being more specific:"))
        print(dim("    agentnode run \"search for AI news\""))
        print(dim("    agentnode run \"extract text from report.pdf\""))
        print(dim("    agentnode run \"translate 'hello' to german\""))
        print()
        return 1

    # Low confidence: prompt for confirmation
    if parsed.confidence == "low" and not dry_run:
        print()
        print(f"  Task: {task}")
        print(f"  Best guess: {bold(parsed.capability)} {dim('(low confidence)')}")
        print()
        alternatives = get_alternatives(task)
        if alternatives:
            print(dim("  Other possibilities:"))
            for alt in alternatives[:3]:
                print(dim(f"    - {alt}"))
            print()
        try:
            confirm = input("  Proceed? [Y/n]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n  Cancelled.")
            return 130
        if confirm.lower() == "n":
            print("  Cancelled.")
            print(dim("  Try `agentnode resolve <capability>` for manual selection."))
            print()
            return 0

    print()

    # Find installed package for this capability
    lock = _read_lock()
    pkgs = lock.get("packages", {})
    target_slug: str | None = None
    target_trust: str = "unknown"

    for slug, info in pkgs.items():
        if parsed.capability in info.get("capability_ids", []):
            target_slug = slug
            target_trust = info.get("trust_level", "unknown")
            break

    # Resolve alternatives for --explain
    alternatives: list = []
    needs_install = target_slug is None

    if needs_install or explain:
        try:
            from agentnode_sdk.client import AgentNodeClient

            client = AgentNodeClient()
            try:
                resolve_result = client.resolve([parsed.capability])
                alternatives = resolve_result.results[:5] if resolve_result.results else []
            finally:
                client.close()
        except Exception:
            alternatives = []

    # --- Explain mode ---
    if explain:
        print(section("Explain"))
        print(kv("Task", task))
        print(kv("Detected capability", parsed.capability))
        print(kv("Confidence", parsed.confidence))
        print(kv("Matched by", parsed.source))
        print()
        if parsed.input_args:
            print(bold("  Extracted input"))
            print("  " + "-" * 15)
            for k, v in parsed.input_args.items():
                print(f"    {k}: {v!r}")
            print()
        if target_slug:
            print(bold("  Selected package"))
            print("  " + "-" * 16)
            print(f"    {target_slug} [{target_trust}] (installed)")
        elif alternatives:
            print(bold("  Selected package"))
            print("  " + "-" * 16)
            best = alternatives[0]
            print(f"    {best.slug} [score: {best.score:.0f}, {best.trust_level}] (will install)")
        print()
        if alternatives and len(alternatives) > 1:
            print(bold("  Alternatives"))
            print("  " + "-" * 12)
            for alt in alternatives[1:4]:
                print(f"    {alt.slug} [score: {alt.score:.0f}, {alt.trust_level}]")
            print()
        if not dry_run:
            print(dim("  ---"))
            print()

    # --- Dry-run mode ---
    if dry_run:
        print(section("Plan (dry-run)"))
        steps = []
        if needs_install:
            if alternatives:
                steps.append(f"Install: {alternatives[0].slug}")
            else:
                steps.append(f"Install: (no package found for {parsed.capability})")
        slug_label = target_slug or (alternatives[0].slug if alternatives else "?")
        args_str = ", ".join(f"{k}={v!r}" for k, v in parsed.input_args.items())
        steps.append(f"Execute: {slug_label}.{parsed.capability}({args_str})")

        for i, step in enumerate(steps, 1):
            print(f"    {i}. {step}")
        print()
        print(dim("  No changes applied."))
        print()
        return 0

    # --- Install if needed ---
    if needs_install:
        if not alternatives:
            print(f"  No package found for capability: {parsed.capability}")
            print(dim("  Try `agentnode search` to find packages manually."))
            print()
            return 1

        # Respect user config for auto-install
        cfg = load_config()
        if cfg.get("auto_upgrade_policy") == "off":
            best = alternatives[0]
            print(f"  Missing capability: {parsed.capability}")
            print(f"  Best match: {best.slug}")
            print()
            print(dim("  Auto-install disabled (auto_upgrade_policy: off)."))
            print(dim(f"  Install manually: agentnode install {best.slug}"))
            print()
            return 1

        best = alternatives[0]

        # Prompt if config requires confirmation
        if cfg.get("install_confirmation") == "prompt":
            print(f"  Missing capability: {parsed.capability}")
            print(f"  Package: {best.slug} [{best.trust_level}]")
            try:
                confirm = input("  Install? [Y/n]: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\n  Cancelled.")
                return 130
            if confirm.lower() == "n":
                print("  Cancelled.")
                return 0
        else:
            print(f"  Missing capability: {parsed.capability}")

        print(f"  Installing {bold(best.slug)}...")

        try:
            from agentnode_sdk.client import AgentNodeClient as _InstClient

            trust_min = cfg.get("trust", {}).get("minimum_trust_level", "verified")
            inst_client = _InstClient()
            try:
                install_result = inst_client.install(
                    best.slug,
                    require_verified=trust_min in ("verified", "trusted", "curated"),
                    require_trusted=trust_min in ("trusted", "curated"),
                )
            finally:
                inst_client.close()

            if not install_result.installed:
                print(f"  Install failed: {install_result.message}")
                print()
                return 1

            target_slug = best.slug
            print(f"  \033[32m[OK]\033[0m {target_slug} installed")
            print()
        except Exception as e:
            print(f"  Auto-install failed: {e}")
            print()
            return 1

    # --- Execute ---
    if not explain:
        print(dim(f"  Task: {task}"))
        print(dim(f"  Capability: {parsed.capability} (confidence: {parsed.confidence})"))
        print()
    print(f"  Running {bold(target_slug)}...")
    print()

    try:
        from agentnode_sdk.runner import run_tool
        from agentnode_sdk.guard import cli_confirmation_callback

        is_interactive_run = os.environ.get("AGENTNODE_NON_INTERACTIVE", "").lower() not in ("true", "1")
        callback = cli_confirmation_callback if is_interactive_run else None

        result = run_tool(target_slug, confirmation_callback=callback, **parsed.input_args)

        if result.mode_used == "policy_denied" and result.policy:
            _print_guard_block(target_slug, None, result)
            return 1

        if not result.success:
            print(f"  \033[31mFailed:\033[0m {result.error}")
            print()
            return 1

        output = result.result if hasattr(result, "result") else result
        if raw:
            print(json.dumps(output, default=str))
        else:
            print(bold("  Result"))
            print("  " + "-" * 6)
            if isinstance(output, dict):
                for k, v in output.items():
                    val_str = str(v)
                    if len(val_str) > 200:
                        val_str = val_str[:197] + "..."
                    print(kv(k, val_str))
            elif isinstance(output, str):
                for line in output.split("\n")[:20]:
                    print(f"  {line}")
            else:
                print(f"  {output}")
            print()
        return 0
    except Exception as e:
        print(f"  \033[31mExecution failed:\033[0m {e}")
        print()
        return 1


def _cmd_run_plan(
    task: str, raw: bool = False, explain: bool = False,
    dry_run: bool = False, json_output: bool = False,
) -> int:
    """Execute a multi-step plan."""
    import os
    from agentnode_sdk.planner import plan_task, plan_and_run, _find_installed_slug, check_plan_risk, audit_plan

    try:
        plan = plan_task(task)
    except ValueError as e:
        print(f"\n  {e}\n", file=sys.stderr)
        return 1

    # --- Plan-level risk warnings ---
    risk_warnings = check_plan_risk(plan)
    if risk_warnings:
        print()
        print(f"  {bold('Plan Risk Warnings')}")
        for w in risk_warnings:
            print(f"  {dim('! ' + w)}")
        print()

    audit_plan(plan, risk_warnings)

    is_interactive = os.environ.get("AGENTNODE_NON_INTERACTIVE", "").lower() not in ("true", "1")

    # --- Low-confidence guardrail ---
    low_conf_steps = [s for s in plan.steps if s.confidence == "low"]
    if low_conf_steps and not dry_run:
        if not is_interactive:
            print(
                f"\n  Aborted: step(s) with low confidence in non-interactive mode.",
                file=sys.stderr,
            )
            for s in low_conf_steps:
                print(f"    Step {s.index + 1}: {s.capability} ({s.confidence})", file=sys.stderr)
            print(file=sys.stderr)
            return 1

        print()
        print(section("Plan"))
        for step in plan.steps:
            conf_warn = " ⚠ low confidence" if step.confidence == "low" else ""
            pipe_label = " <- previous output" if step.uses_previous else ""
            print(f"    {step.index + 1}. [{step.capability}] {step.sub_task}{pipe_label}{conf_warn}")
        print()
        try:
            confirm = input("  Steps with low confidence detected. Proceed? [Y/n]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n  Cancelled.")
            return 130
        if confirm.lower() == "n":
            print("  Cancelled.")
            return 0

    # --- Install confirmation guardrail ---
    cfg = load_config()
    if cfg.get("install_confirmation") == "prompt" and not dry_run:
        missing = []
        for step in plan.steps:
            if _find_installed_slug(step.capability) is None:
                missing.append(step)

        if missing and is_interactive:
            print()
            print(bold("  Packages to install:"))
            for step in missing:
                print(f"    Step {step.index + 1}: {step.capability}")
            print()
            try:
                confirm = input("  Install missing packages? [Y/n]: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\n  Cancelled.")
                return 130
            if confirm.lower() == "n":
                print("  Cancelled.")
                print(dim("  Install manually and re-run."))
                print()
                return 0

    # --- Dry-run ---
    if dry_run:
        print()
        print(section("Plan (dry-run)"))
        for step in plan.steps:
            pipe_label = " <- previous output" if step.uses_previous else ""
            conf_label = f" ({step.confidence})" if step.confidence != "high" else ""
            print(f"    {step.index + 1}. [{step.capability}] {step.sub_task}{pipe_label}{conf_label}")
        print()
        print(dim("  No changes applied."))
        print()
        return 0

    # --- Explain ---
    if explain:
        print()
        print(section("Plan"))
        for step in plan.steps:
            print(f"    Step {step.index + 1}: {bold(step.capability)}")
            print(f"      Task: {step.sub_task}")
            print(f"      Confidence: {step.confidence} ({step.source})")
            if step.uses_previous:
                print(f"      Input: previous step output")
            elif step.input_args:
                args_str = ", ".join(f"{k}={v!r}" for k, v in step.input_args.items())
                print(f"      Input: {args_str}")
            print()
        print(dim("  ---"))
        print()

    # --- Execute ---
    result = plan_and_run(task)

    if json_output:
        print(json.dumps(result.to_dict(), default=str))
        return 0 if result.success else 1

    print()
    for sr in result.steps:
        status = "\033[32m[OK]\033[0m" if sr.success else "\033[31m[FAIL]\033[0m"
        slug_label = f" ({sr.slug})" if sr.slug else ""
        installed_label = " [installed]" if sr.installed else ""
        print(f"  {status} Step {sr.step.index + 1}: {sr.step.capability}{slug_label}{installed_label}")
        if sr.error:
            print(f"       {sr.error}")

    print()

    if result.success and result.steps:
        last = result.steps[-1]
        output = last.result
        if raw:
            print(json.dumps(output, default=str))
        else:
            print(bold("  Result"))
            print("  " + "-" * 6)
            if isinstance(output, dict):
                for k, v in output.items():
                    val_str = str(v)
                    if len(val_str) > 200:
                        val_str = val_str[:197] + "..."
                    print(kv(k, val_str))
            elif isinstance(output, str):
                for line in output.split("\n")[:20]:
                    print(f"  {line}")
            else:
                print(f"  {output}")
            print()

    if not result.success:
        return 1

    print(dim(f"  {len(result.steps)} steps completed in {result.duration_ms:.0f}ms"))
    print()
    return 0


def cmd_remove(capability: str, yes: bool = False) -> int:
    from agentnode_sdk._fileutil import atomic_write_json, file_lock

    lock = read_lockfile()
    if capability not in lock.get("packages", {}):
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

    lock_path = _lockfile_path()
    with file_lock(lock_path):
        lock = read_lockfile(lock_path)
        if capability not in lock.get("packages", {}):
            print(f"\n  {capability} is not installed.\n")
            return 1
        pkg_entry = lock["packages"][capability]
        pkg_type = pkg_entry.get("package_type", "")

        del lock["packages"][capability]
        atomic_write_json(lock_path, lock)

    if pkg_type == "skill":
        from agentnode_sdk.installer import remove_skill_directory
        removed = remove_skill_directory(capability)
        if removed:
            print(f"\n  Removed skill {capability} (lockfile + directory).\n")
        else:
            print(f"\n  Removed {capability} from lockfile (directory already absent).\n")
    else:
        print(f"\n  Removed {capability} from lockfile.\n")
    return 0


def cmd_logs(run_id: str | None = None, limit: int = 10, json_output: bool = False) -> int:
    """Show agent run logs."""
    from agentnode_sdk.run_log import list_runs, read_run

    if run_id:
        events = read_run(run_id)
        if not events:
            print(f"\n  Run {run_id} not found.\n")
            return 1
        if json_output:
            print(json.dumps(events, indent=2, default=str))
        else:
            print()
            print(section(f"Run: {run_id}"))
            for e in events:
                ts = e.get("ts", "")[:19]
                event = e.get("event", "?")
                slug = e.get("slug", "")
                detail = ""
                if event == "tool_call":
                    detail = e.get("tool_name", "")
                elif event == "tool_result":
                    ok = e.get("success", "?")
                    detail = f"success={ok}"
                elif event in ("run_start", "run_end"):
                    detail = slug
                print(f"  {dim(ts)}  {event:<16} {detail}")
            print()
        return 0

    runs = list_runs(limit=limit)
    if not runs:
        print("\n  No run logs found.\n")
        return 0

    if json_output:
        summaries = []
        for rid in runs:
            events = read_run(rid)
            start = next((e for e in events if e.get("event") == "run_start"), {})
            end = next((e for e in events if e.get("event") == "run_end"), {})
            summaries.append({
                "run_id": rid,
                "slug": start.get("slug", ""),
                "started_at": start.get("ts", ""),
                "success": end.get("success"),
                "events": len(events),
            })
        print(json.dumps(summaries, indent=2, default=str))
        return 0

    print()
    print(section("Recent Runs"))
    for rid in runs:
        events = read_run(rid)
        start = next((e for e in events if e.get("event") == "run_start"), {})
        end = next((e for e in events if e.get("event") == "run_end"), {})
        ts = start.get("ts", "")[:19]
        slug = start.get("slug", rid)
        success = end.get("success")
        status = "\033[32mOK\033[0m" if success else "\033[31mFAIL\033[0m" if success is not None else dim("?")
        print(f"  {dim(ts)}  {status}  {slug}  {dim(rid[:12])}")
    print()
    print(dim(f"  Show details: agentnode logs <run_id>"))
    print()
    return 0


def cmd_inspect(slug: str, *, json_output: bool = False) -> int:
    """Security-focused report for an installed package."""
    lock = read_lockfile()
    pkgs = lock.get("packages", {})

    if slug not in pkgs:
        print(f"\n  Package '{slug}' is not installed.\n", file=sys.stderr)
        return 1

    pkg = pkgs[slug]

    # --- Gather audit summary ---
    audit_summary = _inspect_audit_summary(slug)

    if json_output:
        report = _inspect_build_report(slug, pkg, audit_summary)
        print(json.dumps(report, indent=2))
        return 0

    # --- Human-readable output ---
    print()
    print(section(f"Inspect: {slug}"))

    print(kv("Version", pkg.get("version", "?")))
    trust = pkg.get("trust_level") or "unknown"
    last_check = pkg.get("last_trust_check", "")
    trust_display = trust
    if last_check:
        trust_display += f"  (checked: {last_check[:10]})"
    print(kv("Trust level", trust_display))
    print(kv("Package type", pkg.get("package_type", "?")))
    print(kv("Runtime", pkg.get("runtime", "?")))
    print(kv("Installed at", pkg.get("installed_at", "?")))

    # Connector
    connector = pkg.get("connector")
    if connector and isinstance(connector, dict):
        provider = connector.get("provider", "")
        auth_type = connector.get("auth_type", "")
        if provider:
            print(kv("Connector", f"{provider} ({auth_type})" if auth_type else provider))

    # Permissions
    perms = pkg.get("permissions")
    if perms and isinstance(perms, dict):
        print()
        print(f"  {bold('Permissions')}")
        print("  " + "-" * 11)
        for k, v in perms.items():
            label = k.replace("_level", "").replace("_", " ").title()
            print(kv(label, str(v)))
    else:
        print()
        print(f"  {bold('Permissions')}")
        print("  " + "-" * 11)
        print(kv("(none declared)", dim("policy defaults apply")))

    # Capabilities
    caps = pkg.get("capability_ids", [])
    if caps:
        print()
        print(f"  {bold('Capabilities')}")
        print("  " + "-" * 12)
        for c in caps:
            print(f"    {c}")

    # Tools
    tools = pkg.get("tools", [])
    if tools:
        print()
        print(f"  {bold('Tools')}")
        print("  " + "-" * 5)
        for t in tools:
            name = t.get("name", "?")
            print(f"    {name}")

    # Audit summary
    print()
    print(f"  {bold('Audit Summary')}")
    print("  " + "-" * 13)
    if audit_summary["total"] > 0:
        print(kv("Total runs", str(audit_summary["total"])))
        print(kv("Allow", str(audit_summary["allow"])))
        print(kv("Deny", str(audit_summary["deny"])))
        print(kv("Prompt", str(audit_summary["prompt"])))
    else:
        print(kv("(no audit data)", dim("package has not been run yet")))

    # Usage Risk
    from agentnode_sdk.risk_profile import compute_risk_profile
    risk = compute_risk_profile(slug, pkg)
    print()
    print(f"  {bold('Usage Risk')}")
    print("  " + "-" * 10)
    print(kv("Risk level", risk.risk_level.capitalize()))
    print(kv("Risk score", f"{risk.risk_score}/100"))
    if risk.signals:
        print(f"  {dim('Signals:')}")
        for sig in risk.signals:
            print(f"    {dim('- ' + sig)}")
    if risk.risk_flags:
        print(f"  {dim('Flags:')}")
        for flag in risk.risk_flags:
            print(f"    {dim('- ' + flag)}")
    if risk.backend_hint:
        print(f"  {dim('Backend hint:')}")
        for k, v in risk.backend_hint.items():
            print(f"    {dim(f'{k}: {v}')}")

    # Runtime Enforcement
    _inspect_print_enforcement(pkg)

    # Policy Preview
    _inspect_print_policy_preview(slug, pkg)

    # Guard Preview
    _inspect_print_guard_preview(slug, pkg)

    # Privacy
    print()
    print(f"  {bold('Privacy')}")
    print("  " + "-" * 7)
    print(kv("Execution", "local (your machine only)"))
    print(kv("Sent to registry", "install events, search, trust refresh"))
    print(kv("Never sent", "tool inputs, outputs, audit logs"))

    print()
    return 0


def _inspect_print_enforcement(pkg: dict) -> None:
    """Print the Runtime Enforcement section of inspect."""
    runtime = pkg.get("runtime", "python")
    is_subprocess = runtime in ("python", "mcp")

    check = "\033[32m+\033[0m"
    cross = "\033[31m-\033[0m"

    print()
    print(f"  {bold('Runtime Enforcement')}")
    print("  " + "-" * 19)
    if is_subprocess:
        print(kv("Execution mode", "subprocess (env-filtered)"))
        print(kv("Env filtering", f"{check} API keys stripped from subprocess"))
        print(kv("Timeout", f"{check} Subprocess timeout enforced"))
    else:
        print(kv("Execution mode", runtime))
        print(kv("Env filtering", f"{cross} Not applicable ({runtime})"))
        print(kv("Timeout", f"{cross} Not applicable ({runtime})"))

    print(kv("Network isolation", f"{cross} Not enforced at runtime"))
    print(kv("Filesystem isolation", f"{cross} Not enforced at runtime"))
    print()
    print(f"  {dim('Permissions are declared by the publisher and checked')}")
    print(f"  {dim('by the policy gate before execution. Network and filesystem')}")
    print(f"  {dim('access are not restricted at runtime.')}")


def _inspect_print_policy_preview(slug: str, pkg: dict) -> None:
    """Print a dry-run policy preview without triggering interactive prompts."""
    from agentnode_sdk.policy import check_run

    try:
        result = check_run(
            slug,
            tool_name=None,
            kwargs={},
            entry=pkg,
            interactive=False,
        )
    except Exception:
        return

    action = result.action
    if action == "deny":
        display = "\033[31mdeny\033[0m"
    elif action == "prompt":
        display = "\033[33mprompt\033[0m" + dim(" (would ask in interactive mode)")
    else:
        display = "\033[32mallow\033[0m"

    print()
    print(f"  {bold('Policy Preview')} {dim('(current config)')}")
    print("  " + "-" * 14)
    print(kv("Run decision", display))
    print(kv("Source", result.source))
    if result.reason and result.reason != "All checks passed":
        print(kv("Reason", result.reason))


def _inspect_print_guard_preview(slug: str, pkg: dict) -> None:
    """Print guard preview — what check_action() would decide at runtime."""
    from agentnode_sdk.guard import check_action, classify_action

    pkg_type = pkg.get("package_type", "toolpack")

    if pkg_type == "skill":
        print()
        print(f"  {bold('Guard Preview')}")
        print("  " + "-" * 13)
        print(kv("(skills bypass guard)", dim("")))
        return

    if pkg_type == "upgrade":
        print()
        print(f"  {bold('Guard Preview')}")
        print("  " + "-" * 13)
        print(kv("(no executable tools)", dim("upgrade/add-on package")))
        return

    tools = pkg.get("tools", [])
    has_tools = tools and any(isinstance(t, dict) and t.get("name") for t in tools)

    # Resource/prompt-only packages with no executable tools
    if not has_tools and pkg_type not in ("toolpack", "agent"):
        print()
        print(f"  {bold('Guard Preview')}")
        print("  " + "-" * 13)
        print(kv("(no executable tools)", dim(f"{pkg_type} package")))
        return

    print()
    print(f"  {bold('Guard Preview')} {dim('(current config)')}")
    print("  " + "-" * 13)

    if has_tools:
        for t in tools:
            if not isinstance(t, dict) or not t.get("name"):
                continue
            tool_name = t["name"]
            decision = check_action(slug, tool_name, {}, pkg, interactive=False)
            action_types = sorted(decision.action_types)
            act_str = _guard_action_display(decision.action)
            risk_str = _guard_risk_display(decision.risk_level)

            print(f"  {tool_name:<20} {', '.join(action_types):<28} {risk_str:<10} {act_str}")
            if decision.action in ("deny", "prompt"):
                print(f"  {' ' * 20} {dim('↳ ' + decision.reason)}")
    else:
        decision = check_action(slug, None, {}, pkg, interactive=False)
        action_types = sorted(decision.action_types)
        act_str = _guard_action_display(decision.action)
        risk_str = _guard_risk_display(decision.risk_level)

        print(f"  {'(package-level)':<20} {', '.join(action_types):<28} {risk_str:<10} {act_str}")
        if decision.action in ("deny", "prompt"):
            print(f"  {' ' * 20} {dim('↳ ' + decision.reason)}")


def _build_guard_preview(slug: str, pkg: dict) -> list[dict] | str:
    """Build guard preview data for JSON report."""
    from agentnode_sdk.guard import check_action

    pkg_type = pkg.get("package_type", "toolpack")

    if pkg_type == "skill":
        return "skills_bypass_guard"
    if pkg_type == "upgrade":
        return "no_executable_tools"

    tools = pkg.get("tools", [])
    has_tools = tools and any(isinstance(t, dict) and t.get("name") for t in tools)

    if not has_tools and pkg_type not in ("toolpack", "agent"):
        return "no_executable_tools"

    previews: list[dict] = []

    if has_tools:
        for t in tools:
            if not isinstance(t, dict) or not t.get("name"):
                continue
            tool_name = t["name"]
            decision = check_action(slug, tool_name, {}, pkg, interactive=False)
            previews.append({
                "tool_name": tool_name,
                "action_types": sorted(decision.action_types),
                "risk_level": decision.risk_level,
                "guard_action": decision.action,
                "reason": decision.reason,
            })
    else:
        decision = check_action(slug, None, {}, pkg, interactive=False)
        previews.append({
            "tool_name": None,
            "action_types": sorted(decision.action_types),
            "risk_level": decision.risk_level,
            "guard_action": decision.action,
            "reason": decision.reason,
        })

    return previews


def _print_install_guard_summary(slug: str) -> None:
    """Print one-line guard summary after install."""
    from agentnode_sdk.guard import check_action
    from agentnode_sdk.installer import read_lockfile

    try:
        lock = read_lockfile()
        pkg = lock.get("packages", {}).get(slug, {})
        if not pkg:
            return

        pkg_type = pkg.get("package_type", "toolpack")
        if pkg_type in ("skill", "upgrade"):
            return

        tools = pkg.get("tools", [])
        has_tools = tools and any(isinstance(t, dict) and t.get("name") for t in tools)

        # Collect all unique action types and worst decision across tools
        all_action_types: set[str] = set()
        worst_action = "allow"
        worst_risk = "low"
        risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}

        if has_tools:
            for t in tools:
                if not isinstance(t, dict) or not t.get("name"):
                    continue
                decision = check_action(slug, t["name"], {}, pkg, interactive=False)
                all_action_types.update(decision.action_types)
                if _severity(decision.action) > _severity(worst_action):
                    worst_action = decision.action
                if risk_order.get(decision.risk_level, 0) > risk_order.get(worst_risk, 0):
                    worst_risk = decision.risk_level
        else:
            decision = check_action(slug, None, {}, pkg, interactive=False)
            all_action_types.update(decision.action_types)
            worst_action = decision.action
            worst_risk = decision.risk_level

        types_str = ", ".join(sorted(all_action_types))
        act_str = _guard_action_display(worst_action)
        risk_str = _guard_risk_display(worst_risk)

        print(f"  Guard: {types_str} → {act_str} ({risk_str})")

    except Exception:
        pass


def _severity(action: str) -> int:
    return {"allow": 0, "prompt": 1, "deny": 2}.get(action, 0)


def _guard_action_display(action: str) -> str:
    from agentnode_sdk.cli.output import _colors_enabled
    if not _colors_enabled():
        return action
    colors = {"allow": "\033[32m", "deny": "\033[31m", "prompt": "\033[33m"}
    color = colors.get(action, "")
    return f"{color}{action}\033[0m" if color else action


def _guard_risk_display(risk_level: str) -> str:
    from agentnode_sdk.cli.output import _colors_enabled
    if not _colors_enabled():
        return risk_level
    colors = {"critical": "\033[31m", "high": "\033[31m", "medium": "\033[33m", "low": "\033[32m"}
    color = colors.get(risk_level, "")
    return f"{color}{risk_level}\033[0m" if color else risk_level


def _inspect_build_report(slug: str, pkg: dict, audit_summary: dict) -> dict:
    """Build a JSON-serializable inspect report."""
    from agentnode_sdk.risk_profile import compute_risk_profile
    from agentnode_sdk.policy import check_run

    perms = pkg.get("permissions")
    connector = pkg.get("connector")
    risk = compute_risk_profile(slug, pkg)
    risk_dict: dict = {
        "level": risk.risk_level,
        "score": risk.risk_score,
        "signals": risk.signals,
        "flags": risk.risk_flags,
    }
    if risk.backend_hint:
        risk_dict["backend_hint"] = risk.backend_hint

    runtime = pkg.get("runtime", "python")
    is_subprocess = runtime in ("python", "mcp")

    try:
        policy = check_run(slug, None, {}, pkg, interactive=False)
        policy_dict = {
            "action": policy.action,
            "source": policy.source,
            "reason": policy.reason,
        }
    except Exception:
        policy_dict = None

    return {
        "slug": slug,
        "version": pkg.get("version"),
        "package_type": pkg.get("package_type"),
        "trust_level": pkg.get("trust_level"),
        "last_trust_check": pkg.get("last_trust_check"),
        "runtime": runtime,
        "installed_at": pkg.get("installed_at"),
        "permissions": perms,
        "capability_ids": pkg.get("capability_ids", []),
        "tools": [t.get("name", "?") for t in pkg.get("tools", [])],
        "connector": {
            "provider": connector.get("provider"),
            "auth_type": connector.get("auth_type"),
        } if connector and isinstance(connector, dict) else None,
        "audit": audit_summary,
        "risk": risk_dict,
        "enforcement": {
            "execution_mode": "subprocess" if is_subprocess else runtime,
            "env_filtering": is_subprocess,
            "network_isolation": False,
            "filesystem_isolation": False,
            "timeout": is_subprocess,
        },
        "policy_preview": policy_dict,
        "guard": _build_guard_preview(slug, pkg),
        "privacy": {
            "execution_local": True,
            "sent_to_registry": ["install_events", "search_queries", "trust_refresh"],
            "never_sent": ["tool_inputs", "tool_outputs", "audit_logs"],
        },
    }


def _inspect_audit_summary(slug: str) -> dict:
    """Count audit decisions for a specific slug via shared reader."""
    from agentnode_sdk.cli.audit import read_audit_entries

    summary = {"total": 0, "allow": 0, "deny": 0, "prompt": 0}
    for entry in read_audit_entries():
        if entry.get("slug") != slug:
            continue
        action = (entry.get("action") or "").lower()
        summary["total"] += 1
        if action == "allow":
            summary["allow"] += 1
        elif action == "deny":
            summary["deny"] += 1
        elif action == "prompt":
            summary["prompt"] += 1

    return summary


def cmd_capabilities() -> int:
    lock = read_lockfile()
    pkgs = lock.get("packages", {})

    if not pkgs:
        print()
        print("  No capabilities installed.")
        print()
        print(dim("  Run `agentnode discover` to browse available packages."))
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


# ---------------------------------------------------------------------------
# skill list / show
# ---------------------------------------------------------------------------

def cmd_skill_list() -> int:
    lock = read_lockfile()
    pkgs = lock.get("packages", {})

    skills = {
        slug: info for slug, info in pkgs.items()
        if info.get("package_type") == "skill"
    }

    if not skills:
        print()
        print("  No skills installed.")
        print()
        print(dim("  Run `agentnode install <skill>` to install a skill package."))
        print()
        return 0

    print()
    print(section("Installed Skills"))
    for slug, info in skills.items():
        version = info.get("version", "?")
        prompts = info.get("prompts", [])
        prompt_count = len(prompts) if isinstance(prompts, list) else 0
        assets = info.get("assets", [])
        asset_count = len(assets) if isinstance(assets, list) else 0
        meta = []
        if prompt_count:
            meta.append(f"{prompt_count} prompt{'s' if prompt_count != 1 else ''}")
        if asset_count:
            meta.append(f"{asset_count} asset{'s' if asset_count != 1 else ''}")
        suffix = dim(f"  ({', '.join(meta)})") if meta else ""
        print(f"  {bold(slug)} {dim(version)}{suffix}")
    print()
    print(dim(f"  {len(skills)} skill{'s' if len(skills) != 1 else ''} installed"))
    print()
    return 0


def cmd_skill_show(slug: str, render_vars: dict[str, str] | None = None, raw: bool = False) -> int:
    lock = read_lockfile()
    pkgs = lock.get("packages", {})

    if slug not in pkgs:
        print(f"\n  Skill '{slug}' is not installed.\n", file=sys.stderr)
        return 1

    pkg = pkgs[slug]
    if pkg.get("package_type") != "skill":
        print(f"\n  '{slug}' is not a skill (type: {pkg.get('package_type')}).\n", file=sys.stderr)
        return 1

    # --raw: output prompt text only (pipeable)
    if raw:
        from agentnode_sdk.skill import load_skill
        try:
            ctx = load_skill(slug)
            text = ctx.render(**render_vars) if render_vars else ctx.prompt_text
            print(text, end="")
            return 0
        except Exception as exc:
            print(f"Failed to load skill from disk: {exc}", file=sys.stderr)
            return 1

    print()
    print(section(slug))
    print(kv("Version", pkg.get("version", "?")))
    print(kv("Type", "skill"))
    print(kv("Install mode", pkg.get("install_mode", "prompt_only")))
    installed_at = pkg.get("installed_at")
    print(kv("Installed at", installed_at[:19] if installed_at else "?"))
    install_path = pkg.get("install_path", "")
    if install_path:
        print(kv("Install path", install_path))

    prompts = pkg.get("prompts", [])
    if prompts and isinstance(prompts, list):
        print()
        print(f"  {bold('Prompts')}")
        print("  " + "-" * 7)
        for p in prompts:
            if isinstance(p, dict):
                name = p.get("name", "?")
                template = p.get("template", "")
                desc = p.get("description", "")
                line = f"    {bold(name)}"
                if template:
                    line += f"  {dim(template)}"
                print(line)
                if desc:
                    print(f"      {dim(desc)}")

    assets = pkg.get("assets", [])
    if assets and isinstance(assets, list):
        print()
        print(f"  {bold('Assets')}")
        print("  " + "-" * 6)
        for a in assets:
            if isinstance(a, dict):
                aid = a.get("id", "?")
                atype = a.get("type", "?")
                apath = a.get("path", "")
                print(f"    {aid}  {dim(atype)}  {dim(apath)}")

    if render_vars:
        from agentnode_sdk.skill import load_skill
        try:
            ctx = load_skill(slug)
            rendered = ctx.render(**render_vars)
            print()
            print(section("Rendered Prompt"))
            for line in rendered.splitlines():
                print(f"    {line}")
        except Exception as exc:
            print(f"\n  Failed to load skill from disk: {exc}\n", file=sys.stderr)
            return 1

    print()
    return 0


def cmd_init(name: str | None = None, template_type: str | None = None) -> int:
    """Scaffold a new package from template."""
    from agentnode_sdk.cli.init import scaffold_package, prompt_template_choice, prompt_package_details
    from agentnode_sdk.cli.templates import TEMPLATES

    if template_type and template_type in TEMPLATES:
        chosen = template_type
    else:
        chosen = prompt_template_choice()
        if not chosen:
            print("  Cancelled.")
            return 1

    if name:
        details = {
            "package_id": name,
            "name": name,
            "publisher": "your-publisher-slug",
            "summary": f"A {TEMPLATES[chosen]['label'].split('(')[0].strip().lower()} for AI agents",
        }
    else:
        details = prompt_package_details(chosen)
        if not details:
            print("  Cancelled.")
            return 1

    pkg_id = details["package_id"]
    target = Path.cwd() / pkg_id

    if target.exists():
        print(f"  Error: Directory '{pkg_id}' already exists")
        return 1

    target.mkdir(parents=True)
    created = scaffold_package(chosen, target, **details)

    print()
    print(section(f"Created: {pkg_id}"))
    print(kv("Type", TEMPLATES[chosen]["label"].split("(")[0].strip()))
    print(kv("Directory", str(target)))
    print()
    print(bold("  Files"))
    print("  " + "-" * 5)
    for f in sorted(created):
        print(f"    {f}")
    print()
    print(dim("  Next steps:"))
    print(dim(f"    cd {pkg_id}"))
    print(dim("    # Edit the tool code and manifest"))
    print(dim("    agentnode validate ."))
    print(dim("    # Publish via web UI at agentnode.net/publish"))
    print()
    return 0


def cmd_verify_local(path_str: str) -> int:
    """Run verification pipeline locally."""
    import yaml
    from agentnode_sdk.cli.verify_local import run_local_verification

    pkg_path = Path(path_str).resolve()
    if not pkg_path.is_dir():
        print(f"  Error: '{path_str}' is not a directory")
        return 1

    manifest_path = pkg_path / "agentnode.yaml"
    if not manifest_path.exists():
        print("  Error: agentnode.yaml not found")
        return 1

    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  Error parsing manifest: {e}")
        return 1

    pkg_id = manifest.get("package_id", pkg_path.name)
    version = manifest.get("version", "?")

    print()
    print(section(f"Verifying {pkg_id}@{version}"))
    print(dim("  Installing and running verification pipeline locally..."))
    print()

    result = run_local_verification(pkg_path, manifest)

    # Pipeline steps
    print(bold("  Pipeline"))
    print("  " + "-" * 8)
    _step_line("Install", result.install_ok, result.install_log)
    _step_line("Import", result.import_ok, result.import_log)
    _step_line("Smoke", result.smoke_status == "passed", result.smoke_reason)
    if result.tests_ok is not None:
        _step_line("Tests", result.tests_ok, result.tests_log)
    else:
        _step_line("Tests", False, result.tests_log or "not present")
    _step_line("Contract", result.contract_valid)
    _step_line("Reliability", result.reliability >= 0.9, f"{result.reliability:.1%}")
    _step_line("Determinism", result.determinism >= 0.9, f"{result.determinism:.1%}")
    print()

    # Cases
    if result.cases:
        print(bold("  Cases"))
        print("  " + "-" * 5)
        for c in result.cases:
            status = "\033[32m[PASS]\033[0m" if c.passed else "\033[31m[FAIL]\033[0m"
            line = f"  {status} {c.name}"
            if c.duration_ms:
                line += f" ({c.duration_ms}ms)"
            if c.error:
                line += f" — {c.error}"
            print(line)
        print()

    # Score
    print(bold("  Result"))
    print("  " + "-" * 6)
    print(kv("Score", f"{result.score}/95"))
    print(kv("Tier", result.tier.capitalize()))
    print(kv("Mode", result.verification_mode))
    gold = "yes" if result.tier == "gold" else "no"
    print(kv("Gold", gold))
    print()

    if result.warnings:
        for w in result.warnings:
            print(f"  {dim('Warning: ' + w)}")
        print()

    if result.tier == "gold":
        print(dim("  This package will reach Gold tier after publishing."))
    elif result.score >= 80:
        print(dim("  Close to Gold. Check the failed steps above."))
    else:
        print(dim("  See agentnode.net/docs/publishing for Gold requirements."))
    print()

    return 0 if result.install_ok else 1


def cmd_record_cases(path_str: str, strict: bool = False) -> int:
    """Record VCR cassettes for verification cases."""
    import yaml
    from agentnode_sdk.cli.record_cases import record_cases, check_manifest_in, ensure_manifest_in
    from agentnode_sdk.cli.cassette_audit import audit_cassettes

    pkg_path = Path(path_str).resolve()
    if not pkg_path.is_dir():
        print(f"  Error: '{path_str}' is not a directory")
        return 1

    manifest_path = pkg_path / "agentnode.yaml"
    if not manifest_path.exists():
        print("  Error: agentnode.yaml not found")
        return 1

    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  Error parsing manifest: {e}")
        return 1

    pkg_id = manifest.get("package_id", pkg_path.name)

    print()
    print(section(f"Recording cassettes for {pkg_id}"))
    print(dim("  Installing package and calling tools with VCR recording..."))
    print(dim("  (This makes real API calls — ensure credentials are available)"))
    print()

    result = record_cases(pkg_path, manifest)

    # Show results
    if result["recorded"]:
        print(bold("  Recorded"))
        print("  " + "-" * 8)
        for r in result["recorded"]:
            print(f"  \033[32m[OK]\033[0m {r['name']} -> {r['cassette']}")
        print()

    if result["skipped"]:
        print(bold("  Skipped"))
        print("  " + "-" * 7)
        for s in result["skipped"]:
            print(f"  [--] {s['name']}: {s['reason']}")
        print()

    if result["errors"]:
        print(bold("  Errors"))
        print("  " + "-" * 6)
        for e in result["errors"]:
            print(f"  \033[31m[!!]\033[0m {e}")
        print()

    # Check MANIFEST.in
    if result["recorded"]:
        ok, msg = check_manifest_in(pkg_path)
        if not ok:
            ensure_manifest_in(pkg_path)
            print(dim("  Created/updated MANIFEST.in to include fixtures."))
            print()

    # Audit cassettes for dynamic/sensitive content
    audit_failed = False
    if result["recorded"]:
        cassette_paths = [pkg_path / r["cassette"] for r in result["recorded"]]
        audit = audit_cassettes(cassette_paths)

        if audit.has_warnings:
            print(bold("  Cassette Warnings"))
            print("  " + "-" * 17)

            secrets = [f for f in audit.findings if f.category == "secret"]
            tokens = [f for f in audit.findings if f.category == "possible_token"]
            dynamic = [f for f in audit.findings if f.category in ("dynamic", "uuid", "timestamp")]

            if secrets:
                print(f"  \033[31m[SECRET]\033[0m Leaked credentials detected:")
                for f in secrets:
                    print(f"    - {f.path}  ({f.value_preview})")
                print()

            if tokens:
                print(f"  \033[33m[TOKEN?]\033[0m Possible tokens/keys:")
                for f in tokens:
                    print(f"    - {f.path}")
                print()

            if dynamic:
                print(f"  \033[33m[DYNAMIC]\033[0m Fields that may change between runs:")
                for f in dynamic:
                    print(f"    - {f.path}  ({f.value_preview})")
                print()

            if secrets:
                print(dim("  ACTION REQUIRED: Remove secrets before committing cassettes."))
                print(dim("  Re-record with credentials in environment, not in cassette."))
                audit_failed = True
            elif dynamic:
                print(dim("  These fields may cause determinism < 1.0 on replay."))
                print(dim("  If verify-local shows determinism issues, consider filtering these."))
            print()

            if strict and (secrets or tokens):
                audit_failed = True

    # Summary
    total = len(result["recorded"]) + len(result["skipped"]) + len(result["errors"])
    print(kv("Total cases", str(total)))
    print(kv("Recorded", str(len(result["recorded"]))))
    print(kv("Skipped", str(len(result["skipped"]))))
    print(kv("Errors", str(len(result["errors"]))))
    print()

    if result["recorded"] and not audit_failed:
        print(dim("  Next: agentnode verify-local ."))
    elif audit_failed:
        print(dim("  Fix cassette warnings above before proceeding."))
    elif result["errors"]:
        print(dim("  Fix the errors above and try again."))
        print(dim("  Common issues: missing API credentials, network errors, wrong input format."))
    print()

    if audit_failed:
        return 1
    return 0 if not result["errors"] else 1


def _step_line(label: str, ok: bool, detail: str = "") -> None:
    status = "\033[32m[PASS]\033[0m" if ok else "\033[31m[FAIL]\033[0m"
    line = f"  {status} {label}"
    if detail:
        line += f"  {dim(detail)}"
    print(line)


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

    if result.risk_level:
        print(bold("  Usage Risk Preview"))
        print("  " + "-" * 18)
        print(kv("Risk level", result.risk_level.capitalize()))
        print(kv("Risk score", f"{result.risk_score}/100"))
        if result.connector_auth_type:
            parts = []
            if result.connector_provider:
                parts.append(result.connector_provider)
            parts.append(result.connector_auth_type)
            print(kv("Connector", " / ".join(parts)))
            if result.connector_scopes:
                print(kv("Scopes", ", ".join(result.connector_scopes)))
        if result.risk_flags:
            print(kv("Flags", ", ".join(result.risk_flags)))
        if result.risk_signals:
            print(kv("Signals", ""))
            for sig in result.risk_signals:
                print(f"    - {sig}")
        print()
        if result.warnings:
            print(bold("  Warnings"))
            print("  " + "-" * 8)
            for w in result.warnings:
                print(f"  ⚠ {w}")
            print()
        if result.risk_hints:
            print(bold("  Hints"))
            print("  " + "-" * 5)
            for hint in result.risk_hints:
                print(f"  • {hint}")
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
