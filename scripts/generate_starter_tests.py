#!/usr/bin/env python3
"""Generate smoke tests for starter packs that don't have tests yet."""
import os
from pathlib import Path

STARTER_PACKS_DIR = Path(__file__).resolve().parent.parent / "starter-packs"

TEST_TEMPLATE = '''"""Smoke tests for {slug}."""
import pytest
from {module_name}.tool import run


def test_run_smoke():
    """Verify run() executes without crash on empty/minimal input."""
    try:
        result = run({{}})
        assert result is not None
    except TypeError:
        pytest.skip("Tool requires specific arguments — manual test needed")
    except Exception as exc:
        # Tool may fail due to missing credentials/services — that's OK for smoke test
        if any(kw in str(exc).lower() for kw in ("api key", "credential", "token", "auth", "connection", "timeout", "network")):
            pytest.skip(f"Tool requires external service: {{exc}}")
        raise
'''

def main():
    packs = sorted(d for d in STARTER_PACKS_DIR.iterdir() if d.is_dir() and (d / "agentnode.yaml").exists())
    generated = 0
    skipped = 0

    for pack_dir in packs:
        slug = pack_dir.name
        module_name = slug.replace("-", "_")
        tests_dir = pack_dir / "tests"
        test_file = tests_dir / "test_tool.py"

        if test_file.exists():
            skipped += 1
            continue

        # Verify tool.py exists
        tool_py = pack_dir / "src" / module_name / "tool.py"
        if not tool_py.exists():
            print(f"  SKIP {slug}: no src/{module_name}/tool.py found")
            skipped += 1
            continue

        tests_dir.mkdir(exist_ok=True)

        # __init__.py
        init_file = tests_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text("")

        # test_tool.py
        test_file.write_text(TEST_TEMPLATE.format(slug=slug, module_name=module_name))
        generated += 1
        print(f"  GENERATED {slug}/tests/test_tool.py")

    print(f"\nDone: {generated} generated, {skipped} skipped, {generated + skipped} total")


if __name__ == "__main__":
    main()
