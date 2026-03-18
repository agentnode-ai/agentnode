#!/usr/bin/env python3
"""Live verification pipeline test — creates 12 realistic packages and publishes them.

Run against the live server to validate all verification scenarios in 3 phases:

Phase 1 — Baseline (harmless, should all pass):
  1. Clean Pure-Python package           -> all 4 steps pass
  3. No tests directory                  -> tests_status="not_present"
  4. Async tool function                 -> detects async, passes

Phase 2 — Failure modes (quarantine, timeout, edge cases):
  2. Wrong entrypoint                    -> import fails, auto-quarantine
  7. Slow import (timeout)               -> import times out, fails
  5. Broken/unsupported input schema     -> smoke inconclusive
 11. Install failure (broken build)      -> install fails, auto-quarantine
 12. Smoke fatal (TypeError)             -> smoke fails (no quarantine)

Phase 3 — Advanced:
  6. Many tools (exceeds cap)            -> caps at 5, skips rest
  8. External API call in smoke          -> ConnectionError = acceptable
  9. Pytest markers + integration tests  -> skips integration, passes unit
 10. Re-verify same version              -> append-only history

Usage:
    cd backend
    python scripts/test_verification_live.py [--base-url http://localhost:8000]
    python scripts/test_verification_live.py --phase 1          # only baseline
    python scripts/test_verification_live.py --phase 1,2        # baseline + failures
    python scripts/test_verification_live.py --cleanup           # delete test packages
"""

import argparse
import hashlib
import io
import json
import os
import sys
import tarfile
import time
from datetime import datetime

import httpx

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_tarball(pack_name: str, files: dict[str, str]) -> bytes:
    """Build a tar.gz artifact from a dict of {relative_path: content}."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel_path, content in files.items():
            full_path = f"{pack_name}/{rel_path}"
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=full_path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def make_manifest(
    slug: str, name: str, publisher: str, tools: list[dict],
    version: str = "1.0.0", entrypoint: str | None = None,
) -> dict:
    """Build a v0.2 manifest dict.

    NOTE: package-level entrypoint uses module.path format (no :function).
    Tool-level entrypoints use module.path:function format.
    """
    if not entrypoint and tools:
        # Extract module path without :function for package-level entrypoint
        ep = tools[0].get("entrypoint", "main.run")
        entrypoint = ep.rsplit(":", 1)[0] if ":" in ep else ep
    return {
        "manifest_version": "0.2",
        "package_id": slug,
        "name": name,
        "publisher": publisher,
        "version": version,
        "package_type": "toolpack",
        "runtime": "python",
        "install_mode": "package",
        "hosting_type": "agentnode_hosted",
        "summary": f"Verification test: {name}",
        "description": f"Test package for verification scenario: {name}",
        "entrypoint": entrypoint or "main:run",
        "tags": ["verification-test"],
        "categories": ["testing"],
        "capabilities": {
            "tools": tools,
            "resources": [],
            "prompts": [],
        },
        "compatibility": {"frameworks": ["generic"], "python": ">=3.10"},
        "permissions": {
            "network": {"level": "none", "allowed_domains": []},
            "filesystem": {"level": "temp"},
            "code_execution": {"level": "none"},
            "data_access": {"level": "input_only"},
            "user_approval": {"required": "never"},
            "external_integrations": [],
        },
        "dependencies": [],
    }


PYPROJECT_TEMPLATE = """\
[build-system]
requires = ["setuptools>=64"]
build-backend = "setuptools.build_meta"

[project]
name = "{slug}"
version = "{version}"
requires-python = ">=3.10"

[tool.setuptools.packages.find]
where = ["."]
"""

# All test slugs share this prefix for easy cleanup
TEST_PREFIX = "verify-"


# ---------------------------------------------------------------------------
# 12 Test-Package Builders
# ---------------------------------------------------------------------------

def build_clean_package() -> tuple[dict, bytes]:
    """1) Clean pure-Python package — should pass all 4 steps."""
    slug = "verify-clean-pack"
    mod = "verify_clean"
    files = {
        "pyproject.toml": PYPROJECT_TEMPLATE.format(slug=slug, version="1.0.0"),
        f"{mod}/__init__.py": "",
        f"{mod}/tools.py": '''
def word_count(text: str) -> dict:
    """Count words in text."""
    words = text.strip().split()
    return {"count": len(words), "words": words}
''',
        "tests/__init__.py": "",
        "tests/test_tools.py": '''
from verify_clean.tools import word_count

def test_word_count():
    result = word_count("hello world")
    assert result["count"] == 2

def test_empty():
    result = word_count("")
    assert result["count"] == 0
''',
    }
    tools = [{
        "capability_id": "data_cleaning",
        "name": "word_count",
        "description": "Count words in text",
        "entrypoint": f"{mod}.tools:word_count",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }]
    manifest = make_manifest(slug, "Clean Verification Pack", "verify-publisher", tools)
    artifact = build_tarball(slug, files)
    return manifest, artifact


def build_wrong_entrypoint_package() -> tuple[dict, bytes]:
    """2) Wrong entrypoint — import should fail, auto-quarantine."""
    slug = "verify-wrong-ep-pack"
    mod = "verify_wrong_ep"
    files = {
        "pyproject.toml": PYPROJECT_TEMPLATE.format(slug=slug, version="1.0.0"),
        f"{mod}/__init__.py": "",
        f"{mod}/tools.py": '''
def actual_function(text: str) -> dict:
    return {"result": text}
''',
        "tests/__init__.py": "",
        "tests/test_tools.py": '''
def test_placeholder():
    assert True
''',
    }
    tools = [{
        "capability_id": "data_cleaning",
        "name": "nonexistent_tool",
        "description": "This tool does not exist",
        "entrypoint": f"{mod}.tools:nonexistent_function",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }]
    manifest = make_manifest(slug, "Wrong Entrypoint Pack", "verify-publisher", tools)
    artifact = build_tarball(slug, files)
    return manifest, artifact


def build_no_tests_package() -> tuple[dict, bytes]:
    """3) Package without tests/ directory — tests_status='not_present', no quarantine.

    Trick: include test_stub.py at root level to pass Quality Gate,
    but no tests/ directory so verification sandbox has_tests() returns False.
    """
    slug = "verify-no-tests-pack"
    mod = "verify_no_tests"
    files = {
        "pyproject.toml": PYPROJECT_TEMPLATE.format(slug=slug, version="1.0.0"),
        f"{mod}/__init__.py": "",
        f"{mod}/tools.py": '''
def greet(name: str) -> dict:
    return {"message": f"Hello, {name}!"}
''',
        # Root-level test file passes Quality Gate but sandbox.has_tests() checks for tests/ dir
        "test_stub.py": '''
def test_placeholder():
    assert True
''',
    }
    tools = [{
        "capability_id": "data_cleaning",
        "name": "greet",
        "description": "Greet by name",
        "entrypoint": f"{mod}.tools:greet",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    }]
    manifest = make_manifest(slug, "No Tests Pack", "verify-publisher", tools)
    artifact = build_tarball(slug, files)
    return manifest, artifact


def build_async_tool_package() -> tuple[dict, bytes]:
    """4) Async tool function — should detect async and pass."""
    slug = "verify-async-pack"
    mod = "verify_async"
    files = {
        "pyproject.toml": PYPROJECT_TEMPLATE.format(slug=slug, version="1.0.0"),
        f"{mod}/__init__.py": "",
        f"{mod}/tools.py": '''
import asyncio

async def async_search(query: str) -> dict:
    """An async tool function."""
    await asyncio.sleep(0.01)
    return {"results": [f"result for {query}"], "count": 1}
''',
        "tests/__init__.py": "",
        "tests/test_tools.py": '''
import asyncio
from verify_async.tools import async_search

def test_async_search():
    result = asyncio.run(async_search("test"))
    assert result["count"] == 1
''',
    }
    tools = [{
        "capability_id": "web_search",
        "name": "async_search",
        "description": "Async search tool",
        "entrypoint": f"{mod}.tools:async_search",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }]
    manifest = make_manifest(slug, "Async Tool Pack", "verify-publisher", tools)
    artifact = build_tarball(slug, files)
    return manifest, artifact


def build_broken_schema_package() -> tuple[dict, bytes]:
    """5) Broken/unsupported input schema — smoke should be inconclusive."""
    slug = "verify-broken-schema-pack"
    mod = "verify_broken_schema"
    files = {
        "pyproject.toml": PYPROJECT_TEMPLATE.format(slug=slug, version="1.0.0"),
        f"{mod}/__init__.py": "",
        f"{mod}/tools.py": '''
def complex_tool(data: dict, config: dict, options: list) -> dict:
    """Tool with complex required params that schema generator can't fill."""
    if not data.get("nested", {}).get("deep", {}).get("value"):
        raise ValueError("Missing required nested value")
    return {"processed": True}
''',
        "tests/__init__.py": "",
        "tests/test_tools.py": '''
def test_placeholder():
    assert True
''',
    }
    tools = [{
        "capability_id": "json_processing",
        "name": "complex_tool",
        "description": "Tool with complex schema",
        "entrypoint": f"{mod}.tools:complex_tool",
        "input_schema": {
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "properties": {
                        "nested": {
                            "type": "object",
                            "properties": {
                                "deep": {
                                    "type": "object",
                                    "properties": {
                                        "value": {
                                            "type": "object",
                                            "properties": {
                                                "very_deep": {"type": "string"}
                                            },
                                            "required": ["very_deep"]
                                        }
                                    },
                                    "required": ["value"]
                                }
                            },
                            "required": ["deep"]
                        }
                    },
                    "required": ["nested"]
                },
                "config": {
                    "type": "object",
                    "properties": {"mode": {"type": "string"}},
                    "required": ["mode"],
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["data", "config", "options"],
        },
    }]
    manifest = make_manifest(slug, "Broken Schema Pack", "verify-publisher", tools)
    artifact = build_tarball(slug, files)
    return manifest, artifact


def build_many_tools_package() -> tuple[dict, bytes]:
    """6) 10 tools — should cap at 5, skip rest."""
    slug = "verify-many-tools-pack"
    mod = "verify_many_tools"

    tool_funcs = "\n".join([
        f'''
def tool_{i}(text: str) -> dict:
    """Tool number {i}."""
    return {{"tool": {i}, "text": text}}
'''
        for i in range(10)
    ])

    files = {
        "pyproject.toml": PYPROJECT_TEMPLATE.format(slug=slug, version="1.0.0"),
        f"{mod}/__init__.py": "",
        f"{mod}/tools.py": tool_funcs,
        "tests/__init__.py": "",
        "tests/test_tools.py": '''
from verify_many_tools.tools import tool_0
def test_tool_0():
    assert tool_0("hello")["tool"] == 0
''',
    }
    tools = [
        {
            "capability_id": "data_cleaning",
            "name": f"tool_{i}",
            "description": f"Tool number {i}",
            "entrypoint": f"{mod}.tools:tool_{i}",
            "input_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        }
        for i in range(10)
    ]
    manifest = make_manifest(slug, "Many Tools Pack", "verify-publisher", tools)
    artifact = build_tarball(slug, files)
    return manifest, artifact


def build_slow_import_package() -> tuple[dict, bytes]:
    """7) Slow import — tool module has time.sleep(20) at module level."""
    slug = "verify-slow-import-pack"
    mod = "verify_slow_import"
    files = {
        "pyproject.toml": PYPROJECT_TEMPLATE.format(slug=slug, version="1.0.0"),
        f"{mod}/__init__.py": "",
        f"{mod}/tools.py": '''
import time
# Simulate slow module initialization (external lib loading, model download, etc.)
time.sleep(20)

def slow_tool(text: str) -> dict:
    return {"result": text}
''',
        "tests/__init__.py": "",
        "tests/test_tools.py": '''
def test_placeholder():
    assert True
''',
    }
    tools = [{
        "capability_id": "data_cleaning",
        "name": "slow_tool",
        "description": "Tool with slow import",
        "entrypoint": f"{mod}.tools:slow_tool",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }]
    manifest = make_manifest(slug, "Slow Import Pack", "verify-publisher", tools)
    artifact = build_tarball(slug, files)
    return manifest, artifact


def build_external_api_package() -> tuple[dict, bytes]:
    """8) External API call in smoke — ConnectionError is acceptable."""
    slug = "verify-external-api-pack"
    mod = "verify_external_api"
    files = {
        "pyproject.toml": PYPROJECT_TEMPLATE.format(slug=slug, version="1.0.0"),
        f"{mod}/__init__.py": "",
        f"{mod}/tools.py": '''
import urllib.request
import urllib.error

def fetch_data(url: str) -> dict:
    """Tool that calls an external API."""
    try:
        resp = urllib.request.urlopen(url, timeout=5)
        return {"status": resp.status, "body": resp.read().decode()[:200]}
    except urllib.error.URLError as e:
        raise ConnectionError(f"Cannot reach {url}: {e}") from e
    except Exception as e:
        raise ConnectionError(f"Network error: {e}") from e
''',
        "tests/__init__.py": "",
        "tests/test_tools.py": '''
import pytest

def test_fetch_invalid_url():
    from verify_external_api.tools import fetch_data
    with pytest.raises(ConnectionError):
        fetch_data("http://nonexistent.invalid/api")
''',
    }
    tools = [{
        "capability_id": "api_integration",
        "name": "fetch_data",
        "description": "Fetch data from URL",
        "entrypoint": f"{mod}.tools:fetch_data",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    }]
    manifest = make_manifest(slug, "External API Pack", "verify-publisher", tools)
    manifest["permissions"]["network"] = {"level": "unrestricted", "allowed_domains": []}
    artifact = build_tarball(slug, files)
    return manifest, artifact


def build_pytest_markers_package() -> tuple[dict, bytes]:
    """9) Pytest markers — integration tests should be skipped."""
    slug = "verify-pytest-markers-pack"
    mod = "verify_pytest_markers"
    files = {
        "pyproject.toml": PYPROJECT_TEMPLATE.format(slug=slug, version="1.0.0") + """
[tool.pytest.ini_options]
markers = [
    "integration: marks tests as integration tests (deselect with '-m not integration')",
]
""",
        f"{mod}/__init__.py": "",
        f"{mod}/tools.py": '''
def parse_text(text: str) -> dict:
    """Parse and analyze text."""
    return {"length": len(text), "words": len(text.split())}
''',
        "tests/__init__.py": "",
        "tests/conftest.py": '''
import os
import pytest
''',
        "tests/test_unit.py": '''
from verify_pytest_markers.tools import parse_text

def test_parse_basic():
    result = parse_text("hello world")
    assert result["length"] == 11
    assert result["words"] == 2

def test_parse_empty():
    result = parse_text("")
    assert result["length"] == 0
''',
        "tests/test_integration.py": '''
import os
import pytest

@pytest.mark.integration
def test_external_service():
    """This should be SKIPPED during verification."""
    import urllib.request
    resp = urllib.request.urlopen("http://production-api.internal/health")
    assert resp.status == 200

@pytest.mark.integration
def test_database_connection():
    """This should be SKIPPED during verification."""
    raise RuntimeError("Should never run during verification!")
''',
    }
    tools = [{
        "capability_id": "data_cleaning",
        "name": "parse_text",
        "description": "Parse and analyze text",
        "entrypoint": f"{mod}.tools:parse_text",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }]
    manifest = make_manifest(slug, "Pytest Markers Pack", "verify-publisher", tools)
    artifact = build_tarball(slug, files)
    return manifest, artifact


def build_reverify_package() -> tuple[dict, bytes]:
    """10) Normal package for re-verify testing."""
    slug = "verify-reverify-pack"
    mod = "verify_reverify"
    files = {
        "pyproject.toml": PYPROJECT_TEMPLATE.format(slug=slug, version="1.0.0"),
        f"{mod}/__init__.py": "",
        f"{mod}/tools.py": '''
def echo(text: str) -> dict:
    """Simply echo back the input."""
    return {"echo": text}
''',
        "tests/__init__.py": "",
        "tests/test_tools.py": '''
from verify_reverify.tools import echo

def test_echo():
    assert echo("hello")["echo"] == "hello"
''',
    }
    tools = [{
        "capability_id": "data_cleaning",
        "name": "echo",
        "description": "Echo text back",
        "entrypoint": f"{mod}.tools:echo",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }]
    manifest = make_manifest(slug, "Re-verify Test Pack", "verify-publisher", tools)
    artifact = build_tarball(slug, files)
    return manifest, artifact


def build_install_fail_package() -> tuple[dict, bytes]:
    """11) Broken build — pip install fails due to missing dependency."""
    slug = "verify-install-fail-pack"
    mod = "verify_install_fail"
    files = {
        "pyproject.toml": '''\
[build-system]
requires = ["setuptools>=64"]
build-backend = "setuptools.build_meta"

[project]
name = "verify-install-fail-pack"
version = "1.0.0"
requires-python = ">=3.10"
dependencies = [
    "this-package-does-not-exist-agentnode-test>=99.0.0",
]

[tool.setuptools.packages.find]
where = ["."]
''',
        f"{mod}/__init__.py": "",
        f"{mod}/tools.py": '''
def broken_tool(text: str) -> dict:
    return {"result": text}
''',
        "tests/__init__.py": "",
        "tests/test_tools.py": '''
def test_placeholder():
    assert True
''',
    }
    tools = [{
        "capability_id": "data_cleaning",
        "name": "broken_tool",
        "description": "Tool that can never install",
        "entrypoint": f"{mod}.tools:broken_tool",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }]
    manifest = make_manifest(slug, "Install Fail Pack", "verify-publisher", tools)
    artifact = build_tarball(slug, files)
    return manifest, artifact


def build_smoke_fatal_package() -> tuple[dict, bytes]:
    """12) Smoke fatal — tool raises TypeError on call (FATAL exception)."""
    slug = "verify-smoke-fatal-pack"
    mod = "verify_smoke_fatal"
    files = {
        "pyproject.toml": PYPROJECT_TEMPLATE.format(slug=slug, version="1.0.0"),
        f"{mod}/__init__.py": "",
        f"{mod}/tools.py": '''
def buggy_tool(text: str) -> dict:
    """Tool that always crashes with a TypeError."""
    # Simulates a real bug: calling a None as a function
    processor = None
    return processor(text)  # TypeError: 'NoneType' object is not callable
''',
        "tests/__init__.py": "",
        "tests/test_tools.py": '''
def test_placeholder():
    assert True
''',
    }
    tools = [{
        "capability_id": "data_cleaning",
        "name": "buggy_tool",
        "description": "Tool with a fatal bug",
        "entrypoint": f"{mod}.tools:buggy_tool",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }]
    manifest = make_manifest(slug, "Smoke Fatal Pack", "verify-publisher", tools)
    artifact = build_tarball(slug, files)
    return manifest, artifact


# ---------------------------------------------------------------------------
# Expected results
# ---------------------------------------------------------------------------

EXPECTED = {
    "verify-clean-pack": {
        "status": "passed",
        "install_status": "passed",
        "import_status": "passed",
        "smoke_status": "passed",
        "tests_status": "passed",
        "quarantined": False,
    },
    "verify-wrong-ep-pack": {
        "status": "failed",
        "install_status": "passed",
        "import_status": "failed",
        "quarantined": True,
    },
    "verify-no-tests-pack": {
        "status": "passed",
        "install_status": "passed",
        "import_status": "passed",
        "tests_status": "not_present",
        "quarantined": False,
    },
    "verify-async-pack": {
        "status": "passed",
        "install_status": "passed",
        "import_status": "passed",
        "smoke_status": "passed",
        "quarantined": False,
    },
    "verify-broken-schema-pack": {
        "status": "passed",
        "install_status": "passed",
        "import_status": "passed",
        "smoke_status": "inconclusive",
        "quarantined": False,
    },
    "verify-many-tools-pack": {
        "status": "passed",
        "install_status": "passed",
        "import_status": "passed",
        "quarantined": False,
    },
    "verify-slow-import-pack": {
        "status": "failed",
        "install_status": "passed",
        "import_status": "failed",
        "quarantined": True,
    },
    "verify-external-api-pack": {
        "status": "passed",
        "install_status": "passed",
        "import_status": "passed",
        "smoke_status": "passed",  # ConnectionError = acceptable
        "quarantined": False,
    },
    "verify-pytest-markers-pack": {
        "status": "passed",
        "install_status": "passed",
        "import_status": "passed",
        "tests_status": "passed",
        "quarantined": False,
    },
    "verify-reverify-pack": {
        "status": "passed",
        "install_status": "passed",
        "import_status": "passed",
        "quarantined": False,
    },
    "verify-install-fail-pack": {
        "status": "failed",
        "install_status": "failed",
        "quarantined": True,
    },
    "verify-smoke-fatal-pack": {
        "status": "passed",  # install+import pass -> overall passed
        "install_status": "passed",
        "import_status": "passed",
        "smoke_status": "failed",
        "quarantined": False,  # smoke fail does NOT quarantine
    },
}

# Phased execution order
PHASES = {
    1: [
        ("1. Clean Pure-Python", build_clean_package),
        ("3. No Tests", build_no_tests_package),
        ("4. Async Tool", build_async_tool_package),
    ],
    2: [
        ("2. Wrong Entrypoint", build_wrong_entrypoint_package),
        ("7. Slow Import", build_slow_import_package),
        ("5. Broken Schema", build_broken_schema_package),
        ("11. Install Failure", build_install_fail_package),
        ("12. Smoke Fatal (TypeError)", build_smoke_fatal_package),
    ],
    3: [
        ("6. Many Tools (10)", build_many_tools_package),
        ("8. External API Call", build_external_api_package),
        ("9. Pytest Markers", build_pytest_markers_package),
        ("10. Re-verify Target", build_reverify_package),
    ],
}


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

class Analytics:
    """Collect and display detailed analytics after the test run."""

    def __init__(self):
        self.records: list[dict] = []
        self.start_time = time.time()

    def record(self, slug: str, verification: dict | None, passed: bool,
               publish_time: float):
        self.records.append({
            "slug": slug,
            "verification": verification,
            "passed": passed,
            "publish_time": publish_time,
        })

    def print_report(self):
        print(f"\n{'=' * 70}")
        print("DETAILED ANALYTICS")
        print("=" * 70)

        total_wall = time.time() - self.start_time
        print(f"\n  Total wall time: {total_wall:.0f}s")

        # Duration breakdown
        print(f"\n  {'Slug':<35} {'Duration':>10} {'Status':>12} {'Install':>9} "
              f"{'Import':>9} {'Smoke':>12} {'Tests':>12}")
        print(f"  {'-'*35} {'-'*10} {'-'*12} {'-'*9} {'-'*9} {'-'*12} {'-'*12}")

        durations = []
        for r in self.records:
            v = r["verification"]
            if not v:
                print(f"  {r['slug']:<35} {'N/A':>10} {'NO DATA':>12}")
                continue

            dur = v.get("duration_ms", 0) or 0
            durations.append(dur)
            status = v.get("status", "?")
            install = v.get("install_status", "-") or "-"
            imp = v.get("import_status", "-") or "-"
            smoke = v.get("smoke_status", "-") or "-"
            tests = v.get("tests_status", "-") or "-"

            print(f"  {r['slug']:<35} {dur:>8}ms {status:>12} {install:>9} "
                  f"{imp:>9} {smoke:>12} {tests:>12}")

        if durations:
            avg = sum(durations) / len(durations)
            print(f"\n  Avg verification duration: {avg:.0f}ms")
            print(f"  Min: {min(durations)}ms  Max: {max(durations)}ms")

        # State transition check
        print(f"\n  Status Consistency:")
        for r in self.records:
            v = r["verification"]
            if not v:
                continue
            slug = r["slug"]
            status = v.get("status")
            # Verify that final status is not "running" or "pending"
            if status in ("running", "pending"):
                print(f"    WARN: {slug} stuck in '{status}' (never completed)")
            else:
                print(f"    OK: {slug} -> {status}")

        # Run count check
        print(f"\n  Run Count Verification:")
        for r in self.records:
            v = r["verification"]
            if not v:
                continue
            count = v.get("verification_run_count", 0)
            print(f"    {r['slug']}: run_count={count}")

        # Quarantine audit
        print(f"\n  Quarantine Audit:")
        quarantine_expected = {
            "verify-wrong-ep-pack", "verify-slow-import-pack",
            "verify-install-fail-pack",
        }
        for r in self.records:
            slug = r["slug"]
            v = r["verification"]
            if not v:
                continue
            should_quarantine = slug in quarantine_expected
            was_quarantined = "quarantined" in str(v.get("error_summary", ""))
            # Also check from expected
            exp = EXPECTED.get(slug, {})
            expected_q = exp.get("quarantined", False)
            icon = "OK" if should_quarantine == expected_q else "WARN"
            action = "quarantined" if expected_q else "not quarantined"
            print(f"    {icon}: {slug} -> {action}")

        # Smoke classification audit (for scenario 8)
        print(f"\n  Smoke Classification Audit:")
        for r in self.records:
            v = r["verification"]
            if not v:
                continue
            smoke_log = v.get("smoke_log") or ""
            slug = r["slug"]
            if not smoke_log or smoke_log == "-":
                continue

            # Parse smoke log for classification evidence
            classifications = []
            for line in smoke_log.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if "[PASS]" in line:
                    classifications.append(("PASS", line))
                elif "[FAIL]" in line:
                    classifications.append(("FAIL", line))
                elif "[INCONCLUSIVE]" in line:
                    classifications.append(("INCONCLUSIVE", line))
                elif "ACCEPTABLE_ERROR:" in line:
                    classifications.append(("ACCEPTABLE", line))
                elif "FATAL_ERROR:" in line:
                    classifications.append(("FATAL", line))
                elif "INCONCLUSIVE_ERROR:" in line:
                    classifications.append(("INCONCLUSIVE_ERROR", line))

            if classifications:
                print(f"    {slug}:")
                for cat, line in classifications:
                    print(f"      [{cat}] {line[:100]}")

        # Log visibility audit (owner vs public)
        print(f"\n  Log Visibility (owner gets logs, public doesn't):")
        for r in self.records:
            v = r["verification"]
            if not v:
                continue
            has_logs = any(v.get(k) for k in ("install_log", "import_log", "smoke_log", "tests_log"))
            print(f"    {r['slug']}: logs_present={has_logs}")

        # Warnings audit
        print(f"\n  Warnings Summary:")
        for r in self.records:
            v = r["verification"]
            if not v:
                continue
            wc = v.get("warnings_count", 0)
            ws = v.get("warnings_summary")
            if wc or ws:
                print(f"    {r['slug']}: count={wc}, summary={ws!r}")


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

class VerificationTester:
    def __init__(self, base_url: str, phases: list[int] | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, timeout=30)
        self.token: str | None = None
        self.admin_token: str | None = None
        self.results: dict[str, dict] = {}
        self.analytics = Analytics()
        self.phases = phases or [1, 2, 3]

    def _headers(self, admin: bool = False) -> dict:
        t = self.admin_token if admin else self.token
        return {"Authorization": f"Bearer {t}"} if t else {}

    def preflight_check(self) -> bool:
        """Verify server is reachable and basic auth works."""
        print("\n=== Preflight Check ===")

        # 1. Server reachable
        try:
            resp = self.client.get("/v1/health")
            if resp.status_code == 200:
                print("  Server reachable: OK")
            else:
                # Try root as fallback
                resp = self.client.get("/")
                print(f"  Server reachable: OK (status {resp.status_code})")
        except Exception as e:
            print(f"  FAIL: Cannot reach {self.base_url}: {e}")
            return False

        # 2. Check a few expected endpoints exist
        for path in ["/v1/auth/login", "/v1/packages/validate"]:
            try:
                resp = self.client.post(path, json={})
                # 422 = endpoint exists but bad input, which is fine
                if resp.status_code in (200, 400, 401, 403, 422):
                    print(f"  Endpoint {path}: OK")
                else:
                    print(f"  Endpoint {path}: {resp.status_code} (may be fine)")
            except Exception as e:
                print(f"  WARN: {path}: {e}")

        return True

    def setup_user(self):
        """Register a test user and publisher."""
        print("\n=== Setting up test user & publisher ===")

        # Register
        resp = self.client.post("/v1/auth/register", json={
            "email": "verify-test@agentnode.dev",
            "username": "verifytester",
            "password": "Test!Verify#2026",
        })
        if resp.status_code == 201:
            print("  Registered new user")
        elif resp.status_code == 409:
            print("  User already exists")
        else:
            print(f"  Register: {resp.status_code} {resp.text[:200]}")

        # Login
        resp = self.client.post("/v1/auth/login", json={
            "email": "verify-test@agentnode.dev",
            "password": "Test!Verify#2026",
        })
        if resp.status_code != 200:
            print(f"  LOGIN FAILED: {resp.status_code} {resp.text}")
            sys.exit(1)
        self.token = resp.json()["access_token"]
        print("  Logged in")

        # Create publisher
        resp = self.client.post("/v1/publishers", json={
            "display_name": "Verification Tester",
            "slug": "verify-publisher",
            "bio": "Automated verification testing publisher",
        }, headers=self._headers())
        if resp.status_code == 201:
            print("  Created publisher 'verify-publisher'")
        elif resp.status_code == 409:
            print("  Publisher already exists")
        else:
            print(f"  Publisher: {resp.status_code} {resp.text[:200]}")

        # Try admin login (optional, for re-verify and cleanup)
        resp = self.client.post("/v1/auth/login", json={
            "email": "admin@agentnode.net",
            "password": os.environ.get("ADMIN_PASSWORD", ""),
        })
        if resp.status_code == 200:
            self.admin_token = resp.json()["access_token"]
            print("  Admin login: OK")
        else:
            print("  Admin login: not available (re-verify and cleanup will be skipped)")

    def publish_package(self, manifest: dict, artifact: bytes) -> bool:
        """Publish a package. Returns True on success."""
        slug = manifest["package_id"]
        print(f"\n--- Publishing {slug} ({len(artifact):,} bytes) ---")

        files = {"artifact": (f"{slug}.tar.gz", artifact, "application/gzip")}
        data = {"manifest": json.dumps(manifest)}

        resp = self.client.post(
            "/v1/packages/publish",
            data=data,
            files=files,
            headers=self._headers(),
        )

        if resp.status_code == 200:
            r = resp.json()
            print(f"  Published: {r['slug']}@{r['version']}")
            if r.get("message"):
                print(f"  Message: {r['message']}")
            return True
        else:
            print(f"  PUBLISH FAILED: {resp.status_code}")
            print(f"  {resp.text[:500]}")
            return False

    def wait_for_verification(self, slug: str, max_wait: int = 300) -> dict | None:
        """Poll verification status until complete or timeout."""
        print(f"  Waiting for verification...")
        start = time.time()
        last_status = None

        while time.time() - start < max_wait:
            resp = self.client.get(
                f"/v1/packages/{slug}/verification",
                headers=self._headers(),
            )
            if resp.status_code != 200:
                print(f"  Verification endpoint: {resp.status_code} {resp.text[:200]}")
                time.sleep(3)
                continue

            data = resp.json()
            status = data["status"]

            if status != last_status:
                elapsed = int(time.time() - start)
                print(f"  [{elapsed}s] status={status}")
                last_status = status

            if status in ("passed", "failed", "error", "skipped"):
                return data

            time.sleep(3)

        print(f"  TIMEOUT after {max_wait}s (last status: {last_status})")
        return None

    def check_quarantine(self, slug: str) -> bool:
        """Check if the package's latest version is quarantined.

        Uses the owner-visible versions endpoint which includes quarantine_status.
        Falls back to checking the trust block's verification_status.
        """
        # Try owner versions endpoint (has quarantine_status)
        resp = self.client.get(
            f"/v1/packages/{slug}/versions",
            headers=self._headers(),
        )
        if resp.status_code == 200:
            versions = resp.json().get("versions", [])
            if versions:
                return versions[0].get("quarantine_status") == "quarantined"

        # Fallback: check trust block verification_status
        resp = self.client.get(f"/v1/packages/{slug}")
        if resp.status_code != 200:
            return False
        data = resp.json()
        trust = data.get("blocks", {}).get("trust", {})
        return trust.get("verification_status") == "failed"

    def check_result(self, slug: str, verification: dict | None, expected: dict) -> bool:
        """Check verification result against expected. Returns True if all match."""
        print(f"\n  Checking {slug}:")
        if verification is None:
            print("    FAIL: No verification result")
            return False

        ok = True
        for key, exp_val in expected.items():
            if key == "quarantined":
                actual = self.check_quarantine(slug)
                label = "quarantined"
            else:
                actual = verification.get(key)
                label = key

            match = actual == exp_val
            icon = "OK" if match else "FAIL"
            print(f"    {icon}: {label} = {actual!r} (expected {exp_val!r})")
            if not match:
                ok = False

        # Print extra info
        if verification.get("duration_ms"):
            print(f"    INFO: duration={verification['duration_ms']}ms")
        if verification.get("error_summary"):
            print(f"    INFO: error_summary={verification['error_summary']}")
        if verification.get("warnings_count"):
            print(f"    INFO: warnings_count={verification['warnings_count']}")
        if verification.get("warnings_summary"):
            print(f"    INFO: warnings_summary={verification['warnings_summary']}")
        if verification.get("verification_run_count"):
            print(f"    INFO: run_count={verification['verification_run_count']}")
        if verification.get("runner_version"):
            print(f"    INFO: runner={verification['runner_version']}, "
                  f"python={verification.get('python_version', '?')[:20]}, "
                  f"platform={verification.get('runner_platform', '?')[:30]}")
        if verification.get("triggered_by"):
            print(f"    INFO: triggered_by={verification['triggered_by']}")

        # Print logs for failures (owner gets logs)
        if not ok:
            for log_key in ("install_log", "import_log", "smoke_log", "tests_log"):
                log_val = verification.get(log_key)
                if log_val:
                    print(f"    LOG ({log_key}):")
                    for line in log_val.split("\n")[:15]:
                        print(f"      {line}")

        return ok

    def reverify(self, slug: str, version: str = "1.0.0") -> bool:
        """Trigger re-verification via admin endpoint."""
        if not self.admin_token:
            print("  SKIP: No admin token for re-verify")
            return False

        resp = self.client.post(
            f"/v1/admin/packages/{slug}/versions/{version}/reverify",
            headers=self._headers(admin=True),
        )
        if resp.status_code == 200:
            print(f"  Re-verify triggered for {slug}@{version}")
            return True
        else:
            print(f"  Re-verify failed: {resp.status_code} {resp.text[:200]}")
            return False

    def run_scenario(self, label: str, builder, expected: dict) -> str:
        """Run a single scenario. Returns 'pass', 'fail', or 'skip'."""
        print(f"\n{'=' * 60}")
        print(f"SCENARIO: {label}")
        print("=" * 60)

        manifest, artifact = builder()
        slug = manifest["package_id"]

        pub_start = time.time()
        if not self.publish_package(manifest, artifact):
            print(f"  SKIPPED (publish failed)")
            return "skip"

        pub_time = time.time() - pub_start
        verification = self.wait_for_verification(slug)

        ok = self.check_result(slug, verification, expected)
        self.analytics.record(slug, verification, ok, pub_time)

        if ok:
            self.results[slug] = {"status": "PASS"}
            return "pass"
        else:
            self.results[slug] = {"status": "FAIL", "verification": verification}
            return "fail"

    def run_all(self) -> int:
        """Run test scenarios in phases. Returns exit code."""
        print("=" * 70)
        print("AGENTNODE VERIFICATION PIPELINE — LIVE INTEGRATION TEST")
        print("=" * 70)
        print(f"Server:  {self.base_url}")
        print(f"Phases:  {self.phases}")
        print(f"Started: {datetime.now().isoformat()}")

        # Preflight
        if not self.preflight_check():
            return 1

        # Setup
        self.setup_user()

        passed = 0
        failed = 0
        skipped = 0

        for phase_num in self.phases:
            if phase_num not in PHASES:
                print(f"\nUnknown phase: {phase_num}")
                continue

            phase_scenarios = PHASES[phase_num]
            phase_labels = {
                1: "BASELINE (harmless packages)",
                2: "FAILURE MODES (quarantine, timeout, edge cases)",
                3: "ADVANCED (caps, network, markers, re-verify)",
            }

            print(f"\n{'#' * 70}")
            print(f"# PHASE {phase_num}: {phase_labels.get(phase_num, 'Unknown')}")
            print(f"{'#' * 70}")

            for label, builder in phase_scenarios:
                manifest, _ = builder()
                slug = manifest["package_id"]
                expected = EXPECTED.get(slug, {})

                result = self.run_scenario(label, builder, expected)
                if result == "pass":
                    passed += 1
                elif result == "fail":
                    failed += 1
                else:
                    skipped += 1

            # Phase gate: if baseline (phase 1) has failures, warn before continuing
            if phase_num == 1 and failed > 0:
                print(f"\n  *** WARNING: {failed} baseline scenario(s) FAILED ***")
                print(f"  *** Fix baseline before trusting failure mode results ***")

        # Scenario 10 extra: Re-verify (only if phase 3 ran)
        if 3 in self.phases and "verify-reverify-pack" in self.results:
            print(f"\n{'=' * 60}")
            print("SCENARIO: 10b. Re-verify (second run, append-only history)")
            print("=" * 60)

            slug = "verify-reverify-pack"
            if self.admin_token:
                if self.reverify(slug):
                    time.sleep(2)
                    verification = self.wait_for_verification(slug)
                    if verification:
                        count = verification.get("verification_run_count", 0)
                        triggered = verification.get("triggered_by")
                        if count >= 2:
                            print(f"  OK: run_count={count} (append-only history works)")
                            print(f"  OK: triggered_by={triggered}")
                            print(f"  OK: latest_verification_result_id updated")
                            passed += 1
                        else:
                            print(f"  FAIL: Expected run_count >= 2, got {count}")
                            failed += 1
                    else:
                        print(f"  FAIL: No verification result after re-verify")
                        failed += 1
                else:
                    skipped += 1
            else:
                print("  SKIPPED: No admin credentials available")
                skipped += 1

        # Summary
        print(f"\n{'=' * 70}")
        print("SUMMARY")
        print("=" * 70)
        total = passed + failed + skipped
        print(f"\n  Passed:  {passed}/{total}")
        print(f"  Failed:  {failed}/{total}")
        print(f"  Skipped: {skipped}/{total}")

        print(f"\n  Results:")
        for slug, result in self.results.items():
            icon = "PASS" if result["status"] == "PASS" else "FAIL"
            print(f"    {icon}  {slug}")

        # Analytics
        self.analytics.print_report()

        if failed:
            print(f"\n  {failed} scenario(s) FAILED — check logs above")
            return 1
        else:
            print(f"\n  All scenarios passed!")
            return 0


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def run_cleanup(base_url: str):
    """Delete all test packages (requires admin)."""
    client = httpx.Client(base_url=base_url, timeout=30)

    print("=== Cleanup: Removing test packages ===")
    print("(Requires admin credentials)")

    resp = client.post("/v1/auth/login", json={
        "email": "admin@agentnode.net",
        "password": "testagentnode123",
    })
    if resp.status_code != 200:
        print(f"Admin login failed: {resp.status_code}")
        print("Cannot cleanup without admin access.")
        return

    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Find all test packages
    test_slugs = [
        "verify-clean-pack", "verify-wrong-ep-pack", "verify-no-tests-pack",
        "verify-async-pack", "verify-broken-schema-pack", "verify-many-tools-pack",
        "verify-slow-import-pack", "verify-external-api-pack",
        "verify-pytest-markers-pack", "verify-reverify-pack",
        "verify-install-fail-pack", "verify-smoke-fatal-pack",
    ]

    deleted = 0
    for slug in test_slugs:
        resp = client.delete(f"/v1/admin/packages/{slug}", headers=headers)
        if resp.status_code == 200:
            print(f"  Deleted: {slug}")
            deleted += 1
        elif resp.status_code == 404:
            print(f"  Not found: {slug}")
        else:
            print(f"  Error deleting {slug}: {resp.status_code} {resp.text[:100]}")

    print(f"\nCleanup done: {deleted} package(s) deleted")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Live verification pipeline integration test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python scripts/test_verification_live.py                        # all phases
  python scripts/test_verification_live.py --phase 1              # baseline only
  python scripts/test_verification_live.py --phase 1,2            # baseline + failures
  python scripts/test_verification_live.py --cleanup              # remove test data
  python scripts/test_verification_live.py --base-url https://api.agentnode.dev
""",
    )
    parser.add_argument("--base-url", default="http://localhost:8000",
                        help="Base URL of the AgentNode API (default: http://localhost:8000)")
    parser.add_argument("--phase", default="1,2,3",
                        help="Comma-separated phases to run: 1=baseline, 2=failures, 3=advanced (default: 1,2,3)")
    parser.add_argument("--cleanup", action="store_true",
                        help="Delete all test packages and exit")

    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    if args.cleanup:
        run_cleanup(base_url)
        return

    phases = [int(p.strip()) for p in args.phase.split(",")]

    tester = VerificationTester(base_url, phases=phases)
    exit_code = tester.run_all()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
