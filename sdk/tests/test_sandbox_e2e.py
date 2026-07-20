"""Sprint A — REAL sandbox end-to-end (needs Docker/Podman + the pinned image).

Gated: runs only when AGENTNODE_SANDBOX_E2E=1 AND a real ContainerBackend reports
available (runtime present + pinned image pulled). Otherwise skipped — this never
runs in the normal mocked unit suite.

These tests exercise exactly what P0.0–P0.3 mocked, so they validate the two real
gotchas the mocks could not catch:
  * Bug 1 — the extracted artifact dir (mkdtemp 0700) must be made readable for
    the container's uid 1000 before the /src:ro mount.
  * Bug 2 — the build writes to the /install named volume as --user 1000:1000, so
    the image must pre-own /install (chown 1000:1000).

Run on the Docker host:
    AGENTNODE_SANDBOX_E2E=1 python -m pytest tests/test_sandbox_e2e.py -v
"""
from __future__ import annotations

import os
import subprocess
import textwrap

import pytest

from agentnode_sdk.sandbox import sandbox_volume_name, set_default_backend
from agentnode_sdk.sandbox.container_backend import ContainerBackend

_E2E = os.environ.get("AGENTNODE_SANDBOX_E2E") == "1"

pytestmark = pytest.mark.skipif(
    not _E2E,
    reason="set AGENTNODE_SANDBOX_E2E=1 to run real sandbox E2E (needs Docker/Podman + pinned image)",
)


@pytest.fixture(autouse=True)
def _real_default_backend():
    """Use the REAL ContainerBackend (overrides the conftest fake). Skip loudly if
    no runtime / image — so the operator knows the image isn't pulled yet."""
    be = ContainerBackend()
    av = be.check_available()
    if not av.available:
        pytest.skip(
            f"sandbox not available (runtime/image missing): {av.reason or 'unknown'} "
            "— build+push+pin the image and `agentnode sandbox pull` first"
        )
    set_default_backend(be)
    yield
    set_default_backend(None)


def _runtime() -> str:
    return ContainerBackend().check_available().backend or "docker"


def _rm_volume(name: str) -> None:
    subprocess.run([_runtime(), "volume", "rm", name], capture_output=True)


# --- Headline: real toolpack build into volume + run from volume read-only -----

def test_toolpack_build_and_run_real(tmp_path, monkeypatch):
    from agentnode_sdk import installer
    from tests.hostpolicy import run_python

    # A tiny self-contained toolpack source. run() also reports whether it can see
    # a host secret env var — it must NOT (host env is never passed into the run).
    pkg = tmp_path / "src"
    (pkg / "mytool").mkdir(parents=True)
    (pkg / "mytool" / "__init__.py").write_text("")
    (pkg / "mytool" / "tool.py").write_text(
        "import os\n"
        "def run(x):\n"
        "    return {'doubled': x * 2, 'saw_secret': os.environ.get('AGENTNODE_E2E_HOST_SECRET')}\n"
    )
    (pkg / "setup.py").write_text(textwrap.dedent("""
        from setuptools import setup, find_packages
        setup(name="mytool", version="0.0.1", packages=find_packages())
    """))

    slug, version, ahash = "e2e-pack", "0.0.1", "sha256:" + "a" * 64
    expected_vol = sandbox_volume_name(slug, version, ahash)

    # If a host secret leaks into the container, the test must catch it.
    monkeypatch.setenv("AGENTNODE_E2E_HOST_SECRET", "leak-me")

    # 1. Real containerized build into the volume (validates Bug 1 + Bug 2 fixes).
    volume = installer._container_build_into_volume(slug, version, pkg, ahash)
    assert volume == expected_vol
    try:
        # 2. Real run from the read-only volume via the SDK-free wrapper.
        entry = {
            "version": version, "package_type": "toolpack", "runtime": "python",
            "entrypoint": "mytool.tool:run", "trust_level": "verified",
            "artifact_hash": ahash, "sandboxed": True, "sandbox_volume": volume,
            "permissions": {"network_level": "none"},
        }
        res = run_python(slug, None, entry=entry, x=21)
        assert res.mode_used == "sandbox"
        assert res.success, f"sandbox run failed: {res.error}"
        assert res.result["doubled"] == 42                 # JSON stdin/stdout round-trip
        assert res.result["saw_secret"] is None            # host env isolated
    finally:
        _rm_volume(volume)


def test_run_failclosed_when_volume_removed(tmp_path):
    """A stale lockfile pointing at a missing volume → fail-closed, never host."""
    from tests.hostpolicy import run_python

    slug, version, ahash = "e2e-missing", "0.0.1", "sha256:" + "b" * 64
    entry = {
        "version": version, "package_type": "toolpack", "runtime": "python",
        "entrypoint": "x.y:run", "trust_level": "verified",
        "artifact_hash": ahash, "sandboxed": True,
        "sandbox_volume": sandbox_volume_name(slug, version, ahash),
        "permissions": {"network_level": "none"},
    }
    res = run_python(slug, None, entry=entry)
    assert res.success is False
    assert res.mode_used == "sandbox"
    assert "reinstall" in (res.error or "").lower() or "missing or stale" in (res.error or "").lower()


# --- Real MCP start in the container ------------------------------------------

_MCP_STUB = textwrap.dedent("""
    import sys, json
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        if req.get("method") == "initialize":
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": req.get("id"),
                "result": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "serverInfo": {"name": "e2e-stub", "version": "0"}},
            }) + "\\n")
            sys.stdout.flush()
        # notifications/initialized and anything else: no id -> no response
""")


def test_mcp_starts_in_container_real():
    """A verified MCP (minimal python stdio stub) really starts in the container
    and completes the initialize handshake."""
    from agentnode_sdk.runtimes.mcp_runner import MCPServerProcess

    from tests.hostpolicy import decision, plan
    dec = decision("verified")
    server = MCPServerProcess("e2e-mcp", ["python", "-c", _MCP_STUB], trust_level="verified")
    try:
        server.start(_host_policy_decision=dec, launch_plan=plan("e2e-mcp", dec))  # container + JSON-RPC
        assert server._container_name and server._container_name.startswith("agentnode-mcp-")
    finally:
        server.stop()
