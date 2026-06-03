"""Sprint B — `agentnode sandbox doctor` / `status`: diagnosis + guidance only.

Hard invariants asserted here:
- The doctor NEVER auto-pulls / auto-installs / starts code.
- The ONLY action is an explicit, TTY-confirmed `agentnode sandbox pull`.
- `--json` and non-tty perform NO action (only print the command).
- The eight states are distinguished (incl. placeholder-digest vs image-not-pulled).
"""
import json

import pytest

from agentnode_sdk.cli import sandbox_commands as sc
from agentnode_sdk.cli.sandbox_commands import (
    cmd_sandbox_doctor,
    cmd_sandbox_status,
)
from agentnode_sdk.sandbox.types import SandboxAvailability

REAL_DIGEST = (
    "ghcr.io/agentnode-ai/sandbox@sha256:"
    "6c77561965dc9e98ed9cd0437c4de9aa9171cd3753ae9f11672450ce3125c80f"
)
PLACEHOLDER = "ghcr.io/agentnode-ai/sandbox@sha256:" + "0" * 64


# --- availability factories ---------------------------------------------------

def _av_no_runtime():
    return SandboxAvailability(available=False, backend="none", reason="no container runtime found")

def _av_daemon_down():
    return SandboxAvailability(available=False, backend="docker", reason="daemon not reachable",
                              executable_path="/usr/bin/docker", daemon_ok=False)

def _av_image_missing():
    return SandboxAvailability(available=False, backend="docker", reason="image not present",
                              executable_path="/usr/bin/docker", daemon_ok=True, image_available=False)

def _av_ready():
    return SandboxAvailability(available=True, backend="docker", reason="",
                              executable_path="/usr/bin/docker", daemon_ok=True, image_available=True)


# --- patching helpers ---------------------------------------------------------

def _patch_backend(monkeypatch, av):
    class _FB:
        def check_available(self):
            return av
    monkeypatch.setattr("agentnode_sdk.sandbox.container_backend.ContainerBackend", _FB)

def _patch_image(monkeypatch, image):
    monkeypatch.setattr("agentnode_sdk.sandbox.container_backend._BASE_IMAGE", image)

def _patch_tty(monkeypatch, is_tty):
    class _Stdin:
        def isatty(self):
            return is_tty
    monkeypatch.setattr(sc.sys, "stdin", _Stdin())

def _no_pull(monkeypatch):
    """Replace cmd_sandbox_pull with a call counter; returns the list."""
    calls = []
    monkeypatch.setattr(sc, "cmd_sandbox_pull", lambda: (calls.append(1), 0)[1])
    return calls


@pytest.fixture(autouse=True)
def _default_real_digest(monkeypatch):
    # default to the activated (non-placeholder) digest unless a test overrides
    _patch_image(monkeypatch, REAL_DIGEST)


# === environment doctor: the states ==========================================

def _env_json(monkeypatch, capsys):
    rc = cmd_sandbox_doctor(None, json_output=True)
    data = json.loads(capsys.readouterr().out)
    return rc, data

def test_env_no_runtime(monkeypatch, capsys):
    _patch_backend(monkeypatch, _av_no_runtime())
    rc, data = _env_json(monkeypatch, capsys)
    assert rc == 1 and data["ready"] is False
    runtime = next(c for c in data["checks"] if c["check"] == "runtime")
    assert runtime["ok"] is False and "Docker" in runtime["fix"]

def test_env_daemon_down(monkeypatch, capsys):
    _patch_backend(monkeypatch, _av_daemon_down())
    rc, data = _env_json(monkeypatch, capsys)
    assert rc == 1
    daemon = next(c for c in data["checks"] if c["check"] == "daemon")
    assert daemon["ok"] is False and "BIOS" in daemon["fix"]

def test_env_image_missing(monkeypatch, capsys):
    _patch_backend(monkeypatch, _av_image_missing())
    rc, data = _env_json(monkeypatch, capsys)
    assert rc == 1
    img = next(c for c in data["checks"] if c["check"] == "image")
    assert img["ok"] is False and img["fix"] == "agentnode sandbox pull"

def test_env_placeholder_distinct_from_image_missing(monkeypatch, capsys):
    _patch_image(monkeypatch, PLACEHOLDER)
    _patch_backend(monkeypatch, _av_image_missing())
    rc, data = _env_json(monkeypatch, capsys)
    assert rc == 1
    img = next(c for c in data["checks"] if c["check"] == "image")
    assert img["ok"] is False
    assert "placeholder" in img["detail"].lower()          # distinct state
    assert "pull" not in (img["fix"] or "").lower()        # NOT "run pull"
    assert "Update" in img["fix"]

def test_env_ready(monkeypatch, capsys):
    _patch_backend(monkeypatch, _av_ready())
    rc, data = _env_json(monkeypatch, capsys)
    assert rc == 0 and data["ready"] is True


# === interactive pull offer ===================================================

def test_json_performs_no_action(monkeypatch, capsys):
    """--json never acts, even tty + image missing."""
    _patch_backend(monkeypatch, _av_image_missing())
    _patch_tty(monkeypatch, True)
    calls = _no_pull(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *a, **k: pytest.fail("input() must not be called in --json"))
    cmd_sandbox_doctor(None, json_output=True)
    assert calls == []

def test_offer_non_tty_no_pull(monkeypatch, capsys):
    _patch_backend(monkeypatch, _av_image_missing())
    _patch_tty(monkeypatch, False)
    calls = _no_pull(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *a, **k: pytest.fail("must not prompt on non-tty"))
    rc = cmd_sandbox_doctor(None, json_output=False)
    out = capsys.readouterr().out
    assert rc == 1
    assert "agentnode sandbox pull" in out      # prints the command...
    assert calls == []                          # ...but does NOT pull

def test_offer_tty_no_does_not_pull(monkeypatch, capsys):
    _patch_backend(monkeypatch, _av_image_missing())
    _patch_tty(monkeypatch, True)
    calls = _no_pull(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
    cmd_sandbox_doctor(None, json_output=False)
    assert calls == []

def test_offer_tty_yes_pulls_once(monkeypatch, capsys):
    _patch_backend(monkeypatch, _av_image_missing())
    _patch_tty(monkeypatch, True)
    calls = _no_pull(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")
    cmd_sandbox_doctor(None, json_output=False)
    assert calls == [1]                         # cmd_sandbox_pull called exactly once

def test_no_offer_when_runtime_missing(monkeypatch, capsys):
    """Offer is only for 'image not pulled' — never for missing runtime/daemon."""
    _patch_backend(monkeypatch, _av_no_runtime())
    _patch_tty(monkeypatch, True)
    calls = _no_pull(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *a, **k: pytest.fail("no offer without a runtime"))
    cmd_sandbox_doctor(None, json_output=False)
    assert calls == []


# === per-package doctor =======================================================

def _lock(monkeypatch, packages):
    monkeypatch.setattr("agentnode_sdk.installer.read_lockfile", lambda *a, **k: {"packages": packages})

def test_package_not_installed(monkeypatch, capsys):
    _lock(monkeypatch, {})
    rc = cmd_sandbox_doctor("ghost", json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert rc == 1 and data["ready"] is False
    assert data["checks"][0]["check"] == "installed" and data["checks"][0]["ok"] is False

def test_package_curated_runs_on_host(monkeypatch, capsys):
    _lock(monkeypatch, {"cur": {"version": "1.0", "trust_level": "curated", "runtime": "python"}})
    rc = cmd_sandbox_doctor("cur", json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert rc == 0 and data["ready"] is True
    iso = next(c for c in data["checks"] if c["check"] == "isolation")
    assert iso["ok"] is True and "host" in iso["detail"]

def test_package_community_needs_sandbox(monkeypatch, capsys):
    _patch_backend(monkeypatch, _av_image_missing())
    _lock(monkeypatch, {"com": {"version": "1.0", "trust_level": "verified", "runtime": "python"}})
    rc = cmd_sandbox_doctor("com", json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert rc == 1 and data["ready"] is False
    # isolation marked as "requires the sandbox" + the env image check folded in
    iso = next(c for c in data["checks"] if c["check"] == "isolation")
    assert iso["ok"] is None and "REQUIRES" in iso["detail"]
    assert any(c["check"] == "image" and c["ok"] is False for c in data["checks"])

def test_package_community_toolpack_missing_volume_reinstall(monkeypatch, capsys):
    _patch_backend(monkeypatch, _av_ready())  # sandbox ready, but no built volume
    _lock(monkeypatch, {"tp": {"version": "2.0", "trust_level": "verified", "runtime": "python",
                               "artifact_hash": "sha256:abc"}})  # no sandboxed/sandbox_volume
    rc = cmd_sandbox_doctor("tp", json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert rc == 1 and data["ready"] is False
    bv = next(c for c in data["checks"] if c["check"] == "build_volume")
    assert bv["ok"] is False and "install tp" in bv["fix"]

def test_package_community_toolpack_ready(monkeypatch, capsys):
    from agentnode_sdk.sandbox.container_backend import sandbox_volume_name
    vol = sandbox_volume_name("tp", "2.0", "sha256:abc")
    _patch_backend(monkeypatch, _av_ready())
    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    _lock(monkeypatch, {"tp": {"version": "2.0", "trust_level": "verified", "runtime": "python",
                               "artifact_hash": "sha256:abc", "sandboxed": True, "sandbox_volume": vol}})
    rc = cmd_sandbox_doctor("tp", json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert rc == 0 and data["ready"] is True
    assert any(c["check"] == "build_volume" and c["ok"] is True for c in data["checks"])


# === status one-liner =========================================================

def test_status_ready(monkeypatch, capsys):
    _patch_backend(monkeypatch, _av_ready())
    assert cmd_sandbox_status() == 0
    assert "ready" in capsys.readouterr().out

def test_status_needs_setup_image(monkeypatch, capsys):
    _patch_backend(monkeypatch, _av_image_missing())
    assert cmd_sandbox_status() == 1
    assert "sandbox pull" in capsys.readouterr().out

def test_status_needs_setup_no_runtime(monkeypatch, capsys):
    _patch_backend(monkeypatch, _av_no_runtime())
    assert cmd_sandbox_status() == 1
    assert "Docker" in capsys.readouterr().out


# === pull failure surfaces an auth/private hint ===============================

def test_pull_failure_hints_auth(monkeypatch, capsys):
    # real (non-placeholder) digest via autouse; runtime present; pull returns nonzero
    _patch_backend(monkeypatch, _av_image_missing())
    monkeypatch.setattr(sc.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 1})())
    rc = sc.cmd_sandbox_pull()
    out = capsys.readouterr().out
    assert rc == 1
    assert "docker login ghcr.io" in out
