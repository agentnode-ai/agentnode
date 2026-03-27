#!/usr/bin/env python3
"""Validate all starter packs before publishing.

Checks:
- agentnode.yaml is valid YAML with required fields
- pyproject.toml exists and is parseable
- src/{module}/tool.py exists and has run()
- tests/test_tool.py exists
"""
import re
import sys
from pathlib import Path

import yaml  # PyYAML

STARTER_PACKS_DIR = Path(__file__).resolve().parent.parent / "starter-packs"

REQUIRED_MANIFEST_FIELDS = {"manifest_version", "package_id", "package_type", "name", "publisher", "version", "summary", "runtime", "entrypoint", "capabilities"}
RUN_PATTERN = re.compile(r"^(?:async\s+)?def\s+run\s*\(", re.MULTILINE)


def validate_pack(pack_dir: Path) -> tuple[bool, list[str]]:
    """Validate a single pack. Returns (passed, errors)."""
    errors = []
    slug = pack_dir.name
    module_name = slug.replace("-", "_")

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

    # 3. src/{module}/tool.py with run()
    tool_py = pack_dir / "src" / module_name / "tool.py"
    if not tool_py.exists():
        errors.append(f"src/{module_name}/tool.py missing")
    else:
        content = tool_py.read_text(encoding="utf-8", errors="replace")
        if not RUN_PATTERN.search(content):
            errors.append(f"src/{module_name}/tool.py has no run() function")

    # 4. tests/test_tool.py
    if not (pack_dir / "tests" / "test_tool.py").exists():
        errors.append("tests/test_tool.py missing")

    return len(errors) == 0, errors


def main():
    packs = sorted(d for d in STARTER_PACKS_DIR.iterdir() if d.is_dir() and (d / "agentnode.yaml").exists())

    passed = 0
    failed = 0
    results = []

    for pack_dir in packs:
        ok, errs = validate_pack(pack_dir)
        if ok:
            passed += 1
            results.append((pack_dir.name, "PASS", []))
        else:
            failed += 1
            results.append((pack_dir.name, "FAIL", errs))

    # Print report
    print("=" * 60)
    print("Starter Pack Validation Report")
    print("=" * 60)
    for slug, status, errs in results:
        if status == "FAIL":
            print(f"  FAIL  {slug}")
            for e in errs:
                print(f"        - {e}")
        else:
            print(f"  PASS  {slug}")

    print("=" * 60)
    print(f"Total: {passed + failed} | Passed: {passed} | Failed: {failed}")

    if failed > 0:
        print("\nBLOCKED: Fix failures before publishing.")
        sys.exit(1)
    else:
        print("\nAll packs validated successfully.")


if __name__ == "__main__":
    main()
