"""P0.1 live fail-closed gate in runner.run_tool.

THE critical test: a verified package + no container runtime -> run_tool returns
RunToolResult(success=False, mode_used="sandbox_unavailable"). This proves P0.1
has real, live security value (not just an unused abstraction).

For the allowed tiers (curated/trusted) we stub check_run so reaching it proves
the sandbox gate was passed, without dispatching real tool code.
"""
import logging

import pytest

from agentnode_sdk import installer, runner
from agentnode_sdk.lock_integrity import seal_entry
from agentnode_sdk.policy import PolicyResult
from agentnode_sdk.runner import run_tool
from agentnode_sdk.sandbox import set_default_backend
from agentnode_sdk.sandbox.backend import SandboxBackend
from agentnode_sdk.sandbox.types import SandboxAvailability


class _FakeBackend(SandboxBackend):
    def __init__(self, available: bool):
        self._available = available

    def check_available(self) -> SandboxAvailability:
        if self._available:
            return SandboxAvailability(available=True, backend="docker", reason="",
                                       daemon_ok=True, image_available=True)
        return SandboxAvailability(available=False, backend="none",
                                   reason="no container runtime found")

    def wrap_command(self, spec):
        return ["docker", "run", *spec.command]


@pytest.fixture(autouse=True)
def _reset_default_backend():
    yield
    set_default_backend(None)


def _stub_check_run_deny(monkeypatch):
    """Make check_run return a clean deny so run_tool never dispatches real code;
    reaching it proves the sandbox gate (which runs earlier) was passed."""
    monkeypatch.setattr(
        runner, "check_run",
        lambda *a, **k: PolicyResult(action="deny", reason="stub", source="test"),
    )


def _write_lockfile(tmp_path, slug, trust_level):
    entry = seal_entry({
        "version": "1.0.0",
        "package_type": "toolpack",
        "runtime": "python",
        "trust_level": trust_level,
        "entrypoint": "pk.tool",
        "artifact_hash": "sha256:x",
        "permissions": {},
    })
    lf = tmp_path / "agentnode.lock"
    installer.update_lockfile(slug, entry, path=lf)
    return lf


def test_verified_blocked_when_sandbox_unavailable(tmp_path):
    """THE critical test."""
    set_default_backend(_FakeBackend(available=False))
    lf = _write_lockfile(tmp_path, "evil-pack", "verified")
    res = run_tool("evil-pack", lockfile_path=lf)
    assert res.success is False
    assert res.mode_used == "sandbox_unavailable"


@pytest.mark.parametrize("trust", ["unverified", None, "weird"])
def test_other_nontrusted_blocked_when_unavailable(tmp_path, trust):
    set_default_backend(_FakeBackend(available=False))
    lf = _write_lockfile(tmp_path, "x-pack", trust)
    res = run_tool("x-pack", lockfile_path=lf)
    assert res.mode_used == "sandbox_unavailable"


def test_curated_not_blocked_when_unavailable(tmp_path, monkeypatch):
    set_default_backend(_FakeBackend(available=False))
    _stub_check_run_deny(monkeypatch)
    lf = _write_lockfile(tmp_path, "curated-pack", "curated")
    res = run_tool("curated-pack", lockfile_path=lf)
    assert res.mode_used != "sandbox_unavailable"  # got past the sandbox gate


def test_trusted_not_blocked_but_warns(tmp_path, monkeypatch, caplog):
    set_default_backend(_FakeBackend(available=False))
    _stub_check_run_deny(monkeypatch)
    lf = _write_lockfile(tmp_path, "trusted-pack", "trusted")
    with caplog.at_level(logging.WARNING, logger="agentnode.sandbox"):
        res = run_tool("trusted-pack", lockfile_path=lf)
    assert res.mode_used != "sandbox_unavailable"
    assert any("transition" in r.message.lower() for r in caplog.records)


def test_verified_allowed_when_sandbox_available(tmp_path, monkeypatch):
    """Runtime present -> gate passes (P0.1 still runs on host; routing is P0.2)."""
    set_default_backend(_FakeBackend(available=True))
    _stub_check_run_deny(monkeypatch)
    lf = _write_lockfile(tmp_path, "verified-ok", "verified")
    res = run_tool("verified-ok", lockfile_path=lf)
    assert res.mode_used != "sandbox_unavailable"
