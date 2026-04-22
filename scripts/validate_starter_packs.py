#!/usr/bin/env python3
"""Validate all starter packs before publishing.

Checks per package_type:
- Common: agentnode.yaml (required fields), pyproject.toml
- Toolpack ("package"): src/{module}/tool.py + run() + tests/test_tool.py
- Agent ("agent"): src/{module}/agent.py + async def run( + agent section in manifest
"""
import re
import sys
from pathlib import Path

import yaml  # PyYAML

STARTER_PACKS_DIR = Path(__file__).resolve().parent.parent / "starter-packs"

REQUIRED_MANIFEST_FIELDS = {"manifest_version", "package_id", "package_type", "name", "publisher", "version", "summary", "runtime", "entrypoint", "capabilities"}
RUN_PATTERN = re.compile(r"^(?:async\s+)?def\s+run\s*\(", re.MULTILINE)


def _validate_common(pack_dir: Path, module_name: str) -> tuple[dict | None, list[str]]:
    """Validate fields common to all pack types. Returns (manifest, errors)."""
    errors = []
    manifest = None

    # 1. agentnode.yaml
    manifest_path = pack_dir / "agentnode.yaml"
    if not manifest_path.exists():
        errors.append("agentnode.yaml missing")
    else:
        try:
            with open(manifest_path) as f:
                manifest = yaml.safe_load(f)
            if not isinstance(manifest, dict):
                errors.append("agentnode.yaml is not a valid YAML mapping")
                manifest = None
            else:
                missing = REQUIRED_MANIFEST_FIELDS - set(manifest.keys())
                if missing:
                    errors.append(f"agentnode.yaml missing fields: {', '.join(sorted(missing))}")
                # Check capabilities.tools
                caps = manifest.get("capabilities", {})
                tools = caps.get("tools", [])
                if not tools:
                    errors.append("agentnode.yaml: no tools defined in capabilities")
        except yaml.YAMLError as e:
            errors.append(f"agentnode.yaml parse error: {e}")

    # 2. pyproject.toml
    if not (pack_dir / "pyproject.toml").exists():
        errors.append("pyproject.toml missing")

    return manifest, errors


def _validate_toolpack(pack_dir: Path, module_name: str) -> list[str]:
    """Toolpack-specific checks: tool.py + run() + tests/test_tool.py."""
    errors = []

    tool_py = pack_dir / "src" / module_name / "tool.py"
    if not tool_py.exists():
        errors.append(f"src/{module_name}/tool.py missing")
    else:
        content = tool_py.read_text(encoding="utf-8", errors="replace")
        if not RUN_PATTERN.search(content):
            errors.append(f"src/{module_name}/tool.py has no run() function")

    if not (pack_dir / "tests" / "test_tool.py").exists():
        errors.append("tests/test_tool.py missing")

    return errors


def _validate_agent(pack_dir: Path, module_name: str, manifest: dict | None) -> list[str]:
    """Agent-specific checks: agent.py + async def run( + manifest agent section."""
    errors = []

    agent_py = pack_dir / "src" / module_name / "agent.py"
    if not agent_py.exists():
        errors.append(f"src/{module_name}/agent.py missing")
    else:
        content = agent_py.read_text(encoding="utf-8", errors="replace")
        if not RUN_PATTERN.search(content):
            errors.append(f"src/{module_name}/agent.py has no run() function")

    # agent section required in manifest
    if manifest and not manifest.get("agent"):
        errors.append("agentnode.yaml: agent section missing for package_type=agent")

    # No test file requirement for agents (verification generates auto-tests)

    return errors


def validate_pack(pack_dir: Path) -> tuple[bool, str, list[str]]:
    """Validate a single pack. Returns (passed, pack_type, errors)."""
    slug = pack_dir.name
    module_name = slug.replace("-", "_")

    manifest, errors = _validate_common(pack_dir, module_name)

    # Determine package type from manifest
    pack_type = manifest.get("package_type", "package") if manifest else "package"

    if pack_type == "agent":
        errors.extend(_validate_agent(pack_dir, module_name, manifest))
    else:
        errors.extend(_validate_toolpack(pack_dir, module_name))

    return len(errors) == 0, pack_type, errors


def main():
    packs = sorted(d for d in STARTER_PACKS_DIR.iterdir() if d.is_dir() and (d / "agentnode.yaml").exists())

    passed = 0
    failed = 0
    results = []
    type_counts: dict[str, int] = {}

    for pack_dir in packs:
        ok, pack_type, errs = validate_pack(pack_dir)
        type_counts[pack_type] = type_counts.get(pack_type, 0) + 1
        if ok:
            passed += 1
            results.append((pack_dir.name, "PASS", pack_type, []))
        else:
            failed += 1
            results.append((pack_dir.name, "FAIL", pack_type, errs))

    # Print report
    print("=" * 60)
    print("Starter Pack Validation Report")
    print("=" * 60)
    for slug, status, pack_type, errs in results:
        tag = f"[{pack_type}]"
        if status == "FAIL":
            print(f"  FAIL  {tag:10s} {slug}")
            for e in errs:
                print(f"        - {e}")
        else:
            print(f"  PASS  {tag:10s} {slug}")

    print("=" * 60)
    type_summary = ", ".join(f"{v} {k}s" for k, v in sorted(type_counts.items()))
    print(f"Total: {passed + failed} ({type_summary}) | Passed: {passed} | Failed: {failed}")

    if failed > 0:
        print("\nBLOCKED: Fix failures before publishing.")
        sys.exit(1)
    else:
        print("\nAll packs validated successfully.")


if __name__ == "__main__":
    main()
