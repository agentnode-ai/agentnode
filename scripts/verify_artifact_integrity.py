#!/usr/bin/env python3
"""Verify artifact integrity for all starter packs.

For each artifact, checks:
1. Extractable as tar.gz
2. Contains agentnode.yaml, pyproject.toml, src/, tests/
3. Entrypoint module path matches actual file structure
4. run() function exists and is importable (when dependencies allow)
5. run() signature is inspectable
6. Manifest fields are consistent with artifact contents

Usage:
    python scripts/verify_artifact_integrity.py [--install] [--single SLUG]
"""
import argparse
import importlib
import inspect
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import yaml

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "build" / "artifacts"
STARTER_PACKS_DIR = Path(__file__).resolve().parent.parent / "starter-packs"

RUN_PATTERN = re.compile(r"^(?:async\s+)?def\s+run\s*\(", re.MULTILINE)


def check_artifact(slug: str, install: bool = False) -> tuple[str, list[str], list[str]]:
    """Check a single artifact. Returns (status, errors, warnings)."""
    errors = []
    warnings = []
    artifact_path = ARTIFACTS_DIR / f"{slug}.tar.gz"

    if not artifact_path.exists():
        return "FAIL", [f"Artifact not found: {artifact_path}"], []

    # 1. Extract and inspect structure
    try:
        with tarfile.open(artifact_path, "r:gz") as tar:
            names = tar.getnames()
    except (tarfile.TarError, EOFError) as e:
        return "FAIL", [f"Cannot extract: {e}"], []

    # Normalize paths
    normalized = []
    for n in names:
        parts = n.split("/", 1)
        normalized.append(parts[1] if len(parts) > 1 else parts[0])

    # 2. Required files
    has_manifest = any(f == "agentnode.yaml" for f in normalized)
    has_pyproject = any(f == "pyproject.toml" for f in normalized)
    has_src = any(f.startswith("src/") for f in normalized)
    has_tests = any(f.startswith("tests/") and f.endswith(".py") and "__init__" not in f for f in normalized)

    if not has_manifest:
        errors.append("Missing agentnode.yaml")
    if not has_pyproject:
        errors.append("Missing pyproject.toml")
    if not has_src:
        errors.append("Missing src/ directory")
    if not has_tests:
        warnings.append("Missing test files in artifact")

    # 3. Read and validate manifest from artifact
    manifest = None
    try:
        with tarfile.open(artifact_path, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith("agentnode.yaml"):
                    f = tar.extractfile(member)
                    if f:
                        manifest = yaml.safe_load(f.read())
                    break
    except Exception as e:
        warnings.append(f"Could not read manifest from artifact: {e}")

    if manifest:
        # 4. Entrypoint consistency
        entrypoint = manifest.get("entrypoint", "")
        module_name = slug.replace("-", "_")
        expected_module = f"{module_name}.tool"

        if entrypoint and not entrypoint.startswith(module_name):
            warnings.append(f"Entrypoint '{entrypoint}' doesn't match expected module '{expected_module}'")

        # Check that the entrypoint file exists in artifact
        tool_path = f"src/{module_name}/tool.py"
        if tool_path not in normalized:
            errors.append(f"Entrypoint file not in artifact: {tool_path}")

        # 5. Per-tool entrypoints
        caps = manifest.get("capabilities", {})
        tools = caps.get("tools", [])
        for tool in tools:
            tool_ep = tool.get("entrypoint", "")
            if tool_ep:
                # Validate format: module.path:function
                if ":" in tool_ep:
                    mod_part, func_part = tool_ep.rsplit(":", 1)
                    if not mod_part.startswith(module_name):
                        warnings.append(f"Tool entrypoint '{tool_ep}' doesn't start with '{module_name}'")
                else:
                    if not tool_ep.startswith(module_name):
                        warnings.append(f"Tool entrypoint '{tool_ep}' doesn't start with '{module_name}'")

        # 6. Check run() exists in tool.py source
        try:
            with tarfile.open(artifact_path, "r:gz") as tar:
                for member in tar.getmembers():
                    if member.name.endswith(f"{module_name}/tool.py"):
                        f = tar.extractfile(member)
                        if f:
                            source = f.read().decode("utf-8", errors="replace")
                            if not RUN_PATTERN.search(source):
                                errors.append("run() function not found in tool.py source")
                        break
        except Exception as e:
            warnings.append(f"Could not inspect tool.py source: {e}")

        # 7. Check package_id matches slug
        pkg_id = manifest.get("package_id", "")
        if pkg_id != slug:
            warnings.append(f"package_id '{pkg_id}' doesn't match directory slug '{slug}'")

        # 8. Runtime field
        runtime = manifest.get("runtime", "")
        if runtime not in ("python", "mcp", "remote"):
            errors.append(f"Invalid runtime: '{runtime}'")

    # 9. Optional: pip install + import test
    if install and not errors:
        tmpdir = tempfile.mkdtemp(prefix="anp-verify-")
        try:
            with tarfile.open(artifact_path, "r:gz") as tar:
                tar.extractall(tmpdir)
            pack_dir = Path(tmpdir) / slug
            if not pack_dir.exists():
                # Try without slug prefix
                dirs = [d for d in Path(tmpdir).iterdir() if d.is_dir()]
                pack_dir = dirs[0] if dirs else Path(tmpdir)

            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", str(pack_dir), "--quiet"],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                errors.append(f"pip install failed: {result.stderr[:200]}")
            else:
                # Try importing
                module_name = slug.replace("-", "_")
                try:
                    mod = importlib.import_module(f"{module_name}.tool")
                    if not hasattr(mod, "run"):
                        errors.append("Imported module has no run() attribute")
                    elif not callable(mod.run):
                        errors.append("run is not callable")
                    else:
                        sig = inspect.signature(mod.run)
                        params = list(sig.parameters.keys())
                        if not params:
                            warnings.append("run() takes no parameters")
                except ImportError as e:
                    errors.append(f"Import failed: {e}")
        except subprocess.TimeoutExpired:
            errors.append("pip install timed out")
        except Exception as e:
            warnings.append(f"Install test error: {e}")

    status = "FAIL" if errors else ("WARN" if warnings else "PASS")
    return status, errors, warnings


def main():
    parser = argparse.ArgumentParser(description="Verify artifact integrity")
    parser.add_argument("--install", action="store_true", help="Also test pip install + import")
    parser.add_argument("--single", type=str, help="Check a single pack")
    args = parser.parse_args()

    if args.single:
        slugs = [args.single]
    else:
        slugs = sorted(f.stem.replace(".tar", "") for f in ARTIFACTS_DIR.glob("*.tar.gz"))

    passed = 0
    warned = 0
    failed = 0

    for slug in slugs:
        status, errs, warns = check_artifact(slug, install=args.install)
        if status == "PASS":
            passed += 1
            print(f"  PASS  {slug}")
        elif status == "WARN":
            warned += 1
            print(f"  WARN  {slug}")
            for w in warns:
                print(f"        - {w}")
        else:
            failed += 1
            print(f"  FAIL  {slug}")
            for e in errs:
                print(f"        ! {e}")
            for w in warns:
                print(f"        - {w}")

    print()
    print("=" * 60)
    print(f"Total: {len(slugs)} | Pass: {passed} | Warn: {warned} | Fail: {failed}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
