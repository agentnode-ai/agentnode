"""The MCP preinstall build must cache inside the sandbox /tmp, never in the 16 MiB HOME.

EM2-CACHE-VARS-0011 option 2. Two levels, because the review required both:

* a ProcessSpec assertion pins the exact environment assignment and proves no cache
  variable points into HOME — deterministic, no container needed;
* the real-container checks in ``test_sandbox_e2e.py`` prove that the managers can
  actually create those roots and that a package with a real dependency tree installs.

Before the correction the uv branch failed reproducibly with
``failed to write to file /sandbox-home/.cache/uv/... : No space left on device``.
"""
from __future__ import annotations

import agentnode_sdk.installer as installer
from agentnode_sdk.sandbox.backend import SandboxBackend
from agentnode_sdk.sandbox.types import ProcessSpec, SandboxAvailability


class _CapturingBackend(SandboxBackend):
    """Captures the spec the installer builds without running anything."""

    def __init__(self):
        self.spec: ProcessSpec | None = None

    def check_available(self):
        return SandboxAvailability(available=True, backend="docker", reason="",
                                   daemon_ok=True, image_available=True)

    def wrap_command(self, spec):  # pragma: no cover - not used here
        return ["docker", "run", *spec.command]

    def run_process(self, spec, input_text=None, timeout=120.0):
        self.spec = spec
        # Stop the build immediately: this test is about the spec, not the install.
        return 1, "", "captured"


def _capture(monkeypatch, manager: str, package: str, pkg_version: str) -> ProcessSpec:
    be = _CapturingBackend()
    monkeypatch.setattr(installer, "get_default_backend", lambda: be, raising=False)
    monkeypatch.setattr("agentnode_sdk.sandbox.get_default_backend", lambda: be)
    monkeypatch.setattr(installer.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})())
    try:
        installer._container_build_mcp_volume("s", "1.0", manager, package, pkg_version)
    except Exception:
        pass  # the capturing backend fails the build on purpose
    assert be.spec is not None, "the installer never reached the container build"
    return be.spec


def test_pypi_build_caches_in_tmp_not_home(monkeypatch):
    spec = _capture(monkeypatch, "pypi", "mcp-server-time", "2026.8.18")
    assert spec.env.get("UV_CACHE_DIR") == "/tmp/uv-cache"


def test_npm_build_caches_in_tmp_not_home(monkeypatch):
    spec = _capture(monkeypatch, "npm", "some-mcp", "1.0.0")
    assert spec.env.get("npm_config_cache") == "/tmp/npm-cache"


def test_no_cache_variable_points_into_home(monkeypatch):
    """The whole point: nothing the build sets may resolve under the small HOME, and
    nothing may escape the container's own /tmp — no host path, no shared cache."""
    for manager, package, version in (("pypi", "mcp-server-time", "2026.8.18"),
                                      ("npm", "some-mcp", "1.0.0")):
        spec = _capture(monkeypatch, manager, package, version)
        cache_vars = {k: v for k, v in spec.env.items() if "CACHE" in k.upper()}
        assert cache_vars, f"{manager}: no cache variable is set at all"
        for name, value in cache_vars.items():
            assert value.startswith("/tmp/"), f"{manager}: {name}={value!r} is not under /tmp"
            assert "sandbox-home" not in value, f"{manager}: {name} points into HOME"
            assert not value.startswith("~"), f"{manager}: {name} is HOME-relative"


def test_build_still_asks_for_the_large_tmp_and_a_clean_home(monkeypatch):
    """The correction must not enlarge a limit or weaken the clean HOME."""
    spec = _capture(monkeypatch, "pypi", "mcp-server-time", "2026.8.18")
    assert spec.limits.get("tmp_size") == "512m"
    assert "home_size" not in spec.limits          # HOME stays at the hardened default
    assert spec.clean_home is True
    assert spec.network == "default"               # build-only, unchanged
    assert [m.dst for m in spec.mounts] == ["/install"]   # no new mount
