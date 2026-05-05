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
    print(dim("  Run `agentnode discover` to browse available packages."))
    print(dim("  Run `agentnode search <query>` for full-text search."))
    print(dim("  Run `agentnode recommend` for personalized suggestions."))
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


def cmd_resolve(capabilities: list[str], framework: str | None = None) -> int:
    """Resolve capability IDs to ranked package recommendations."""
    try:
        from agentnode_sdk.client import AgentNodeClient

        client = AgentNodeClient()
        try:
            result = client.resolve(capabilities, framework=framework)
        finally:
            client.close()

        if not result.results:
            print()
            print(f"  No packages found for: {', '.join(capabilities)}")
            print()
            print(dim("  Try `agentnode search <query>` for full-text search."))
            print()
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


def cmd_recommend() -> int:
    """Recommend packages based on what's installed."""
    lock = read_lockfile()
    pkgs = lock.get("packages", {})

    if not pkgs:
        print()
        print("  No packages installed yet.")
        print()
        print(dim("  Run `agentnode discover` to browse available packages."))
        print()
        return 0

    # Collect installed capability IDs and slugs
    installed_slugs = set(pkgs.keys())
    installed_caps: set[str] = set()
    for info in pkgs.values():
        for cap_id in info.get("capability_ids", []):
            installed_caps.add(cap_id)

    # Complementary capability suggestions based on what's installed
    _COMPLEMENTS: dict[str, list[str]] = {
        "web_search": ["text_summarization", "webpage_extraction", "knowledge_graph"],
        "webpage_extraction": ["web_search", "text_summarization", "pdf_extraction"],
        "pdf_extraction": ["text_summarization", "document_parsing", "ocr"],
        "csv_analysis": ["chart_generation", "data_visualization", "spreadsheet_parsing"],
        "text_summarization": ["text_translation", "web_search", "pdf_extraction"],
        "text_translation": ["text_summarization", "language_detection"],
        "browser_navigation": ["webpage_extraction", "web_search", "screenshot_capture"],
        "embedding_generation": ["vector_memory", "text_summarization"],
        "vector_memory": ["embedding_generation", "web_search"],
        "sql_generation": ["csv_analysis", "database_connector"],
        "chart_generation": ["csv_analysis", "data_visualization"],
        "code_analysis": ["code_generation", "test_generation"],
        "code_generation": ["code_analysis", "test_generation"],
    }

    # Find suggestions
    suggested_caps: list[str] = []
    for cap in installed_caps:
        for complement in _COMPLEMENTS.get(cap, []):
            if complement not in installed_caps and complement not in suggested_caps:
                suggested_caps.append(complement)

    if not suggested_caps:
        print()
        print("  No additional recommendations based on your current setup.")
        print()
        print(dim("  Run `agentnode discover --trending` for popular packages."))
        print()
        return 0

    # Resolve suggested capabilities to packages
    try:
        from agentnode_sdk.client import AgentNodeClient

        client = AgentNodeClient()
        try:
            result = client.resolve(suggested_caps[:8], limit=10)
        finally:
            client.close()
    except Exception as e:
        print(f"Recommend failed: {e}", file=sys.stderr)
        return 1

    # Filter out already-installed packages
    recommendations = [
        pkg for pkg in result.results
        if pkg.slug not in installed_slugs
    ]

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

    print(bold("  You might also need:"))
    print("  " + "-" * 20)
    for i, pkg in enumerate(recommendations[:8], 1):
        trust = pkg.trust_level or "unverified"
        matched = ", ".join(pkg.matched_capabilities) if pkg.matched_capabilities else ""
        print(f"  {i}. {bold(pkg.slug)} {dim(f'[{trust}]')}")
        print(f"     {pkg.summary}")
        if matched:
            print(f"     {dim(f'capability: {matched}')}")
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
    print(dim("    agentnode publish ."))
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
