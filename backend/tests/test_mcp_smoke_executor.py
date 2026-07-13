"""Slice 2c-2/2c-3 — sandbox-smoke executor (npm + PyPI, inert by default).

No real network / registry / container runs here: the single `run_container`
seam is injected, and availability is toggled via settings. Tests cover the
fail-closed availability, the should_smoke gating (npm + pypi), the pure
argv/JSON-RPC/parse helpers (npm `npx --offline` + pypi `uv pip install --target`
/ console script), and the full status mapping (pass / hard-block /
review-fallback) for both registries.
"""

from __future__ import annotations

import subprocess

import pytest

from app.mcp import smoke_executor as ex


# --- availability (fail-closed) ---------------------------------------------


def test_disabled_is_unavailable(monkeypatch):
    monkeypatch.setattr(ex.settings, "MCP_SMOKE_MODE", "disabled")
    ok, why = ex.smoke_availability()
    assert ok is False and why == "disabled"


def test_container_mode_without_runtime_is_unavailable(monkeypatch):
    monkeypatch.setattr(ex.settings, "MCP_SMOKE_MODE", "container")
    monkeypatch.setattr(ex, "CONTAINER_RUNTIME", None)
    ok, why = ex.smoke_availability()
    assert ok is False and why == "no_runtime"


def test_run_npm_smoke_unavailable_when_disabled(monkeypatch):
    monkeypatch.setattr(ex.settings, "MCP_SMOKE_MODE", "disabled")
    calls = []
    res = ex.run_smoke(
        _manifest(), _sv(), run_container=lambda *a, **k: calls.append(a) or (0, "", "")
    )
    assert res["status"] == "unavailable"
    assert res["failure_reason"] == "disabled"
    assert calls == []  # fail-closed: no container touched


# --- should_smoke gating -----------------------------------------------------


def _manifest(command=None, env_keys=None, npm="@scope/mcp"):
    server = {"npm_package": npm, "command": command or ["npx", "-y", f"{npm}@1.2.3"]}
    if env_keys:
        server["env_keys"] = env_keys
    return {"runtime": "mcp", "package_id": "scope-mcp", "mcp_server": server}


def _sv(registry="npm", exists=True, version="1.2.3", pinning="pinned"):
    return {
        "registry": registry,
        "package_name": "@scope/mcp",
        "package_exists": exists,
        "resolved_version": version,
        "command_pinning": pinning,
    }


def _pypi_manifest():
    return {
        "runtime": "mcp",
        "package_id": "time-mcp",
        "mcp_server": {
            "pypi_package": "mcp-server-time",
            "command": ["uvx", "mcp-server-time==2026.7.10"],
        },
    }


def _pypi_sv(exists=True, version="2026.7.10", pinning="pinned"):
    return {
        "registry": "pypi",
        "package_name": "mcp-server-time",
        "package_exists": exists,
        "resolved_version": version,
        "command_pinning": pinning,
    }


def test_should_smoke_npm_public_pinned_true():
    run, skip, reason = ex.should_smoke(_manifest(), _sv())
    assert run is True and skip is None


def test_should_smoke_credentialed_is_skipped():
    run, skip, reason = ex.should_smoke(_manifest(env_keys=["API_KEY"]), _sv())
    assert run is False and skip == "skipped" and reason == "credentialed"


def test_should_smoke_pypi_public_pinned_true():
    # 2c-3: PyPI is now supported (was skipped in 2c-2).
    run, skip, reason = ex.should_smoke(_pypi_manifest(), _pypi_sv())
    assert run is True and skip is None


def test_should_smoke_unsupported_registry_is_skipped():
    run, skip, reason = ex.should_smoke(_manifest(), _sv(registry="cargo"))
    assert run is False and skip == "skipped" and reason == "unsupported_registry"


def test_should_smoke_pypi_credentialed_is_skipped():
    m = _pypi_manifest()
    m["mcp_server"]["env_keys"] = ["TOKEN"]
    run, skip, reason = ex.should_smoke(m, _pypi_sv())
    assert run is False and skip == "skipped" and reason == "credentialed"


def test_should_smoke_pypi_unpinned_is_precondition():
    run, skip, reason = ex.should_smoke(
        _pypi_manifest(), _pypi_sv(pinning="unpinned_resolved")
    )
    assert run is False and skip is None and reason is None


def test_should_smoke_not_public_is_precondition_no_record():
    run, skip, reason = ex.should_smoke(_manifest(), _sv(exists=False, version=None))
    assert run is False and skip is None and reason is None


def test_should_smoke_unpinned_is_precondition_no_record():
    run, skip, reason = ex.should_smoke(_manifest(), _sv(pinning="unpinned_resolved"))
    assert run is False and skip is None and reason is None


# --- pure helpers ------------------------------------------------------------


def test_install_argv_has_network_writable_volume_and_root_user():
    argv = ex.install_argv("img", "vol", "@scope/mcp", "1.2.3")
    assert "--network" in argv and argv[argv.index("--network") + 1] == "default"
    assert "-v" in argv and "vol:/app:rw" in argv
    assert "img" in argv
    assert any("@scope/mcp@1.2.3" in a for a in argv)
    # Host verification (2c-2): a fresh named volume is root-owned -> phase 1 must
    # install as root, else EACCES. Still hardened (cap-drop etc).
    assert argv[argv.index("--user") + 1] == "0:0"
    assert "--cap-drop=ALL" in argv


def test_run_argv_is_network_none_readonly_volume_nonroot():
    argv = ex.run_argv("img", "vol", "@scope/mcp", "1.2.3")
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv
    assert "vol:/app:ro" in argv
    assert "--offline" in argv
    # The security-critical run phase stays non-root.
    assert argv[argv.index("--user") + 1] == "1000:1000"
    assert "--cap-drop=ALL" in argv


def test_handshake_frames_contain_initialize_and_tools_list():
    import json

    frames = [json.loads(li) for li in ex.handshake_frames().splitlines() if li.strip()]
    methods = [f.get("method") for f in frames]
    assert "initialize" in methods
    assert "notifications/initialized" in methods
    assert "tools/list" in methods


def test_parse_handshake_passed():
    out = "\n".join(
        [
            "some server log line",
            '{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05"}}',
            '{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"a"},{"name":"b"}]}}',
        ]
    )
    p = ex.parse_handshake_output(out)
    assert p["init_result"] is True and p["tools_count"] == 2 and p["any_json"] is True


def test_parse_handshake_initialize_error():
    out = '{"jsonrpc":"2.0","id":1,"error":{"code":-1,"message":"nope"}}'
    p = ex.parse_handshake_output(out)
    assert p["init_error"] is True and p["init_result"] is False


def test_parse_handshake_malformed():
    p = ex.parse_handshake_output("{not json")
    assert p["malformed"] is True and p["any_json"] is False


# --- full status mapping via injected run_container --------------------------


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(ex.settings, "MCP_SMOKE_MODE", "container")
    monkeypatch.setattr(ex, "CONTAINER_RUNTIME", "docker")
    # image present
    monkeypatch.setattr(ex, "smoke_availability", lambda: (True, ""))


def _fake_runner(install=(0, "", ""), run=(0, "", ""), install_exc=None, run_exc=None):
    """Return a run_container stub: 1st call = install, 2nd = run, 3rd = volume rm."""
    state = {"n": 0}

    def runner(argv, input_text, timeout):
        state["n"] += 1
        if state["n"] == 1:
            if install_exc:
                raise install_exc
            return install
        if state["n"] == 2:
            if run_exc:
                raise run_exc
            return run
        return (0, "", "")  # volume rm

    return runner


def _passing_stdout():
    return (
        '{"jsonrpc":"2.0","id":1,"result":{}}\n'
        '{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"a"}]}}'
    )


def test_success_maps_to_passed(enabled):
    res = ex.run_smoke(
        _manifest(), _sv(), run_container=_fake_runner(run=(0, _passing_stdout(), ""))
    )
    assert res["status"] == "passed"
    assert res["tools_count"] == 1
    assert res["initialized"] is True
    assert res["runtime"] == "npm" and res["version"] == "1.2.3"
    assert res["command_hash"] and res["image_digest"]


def test_install_fail_is_review_fallback(enabled):
    res = ex.run_smoke(
        _manifest(), _sv(), run_container=_fake_runner(install=(1, "", "boom"))
    )
    assert res["status"] == "failed" and res["failure_reason"] == "install_failed"


def test_install_timeout_is_review_fallback(enabled):
    res = ex.run_smoke(
        _manifest(),
        _sv(),
        run_container=_fake_runner(install_exc=subprocess.TimeoutExpired("x", 1)),
    )
    assert res["failure_reason"] == "timeout"


def test_startup_crash_is_hard(enabled):
    # no JSON on stdout + non-zero exit -> startup_crash (hard block)
    res = ex.run_smoke(
        _manifest(), _sv(), run_container=_fake_runner(run=(1, "boom traceback", ""))
    )
    assert res["status"] == "failed" and res["failure_reason"] == "startup_crash"


def test_protocol_error_is_hard(enabled):
    # ran (rc 0) but emitted no valid JSON-RPC -> protocol_error (hard)
    res = ex.run_smoke(
        _manifest(), _sv(), run_container=_fake_runner(run=(0, "hello not json", ""))
    )
    assert res["failure_reason"] == "protocol_error"


def test_initialize_fail_is_review(enabled):
    out = '{"jsonrpc":"2.0","id":1,"error":{"code":-1,"message":"no"}}'
    res = ex.run_smoke(_manifest(), _sv(), run_container=_fake_runner(run=(0, out, "")))
    assert res["failure_reason"] == "initialize_failed"


def test_tools_list_fail_after_init_is_hard(enabled):
    out = (
        '{"jsonrpc":"2.0","id":1,"result":{}}\n'
        '{"jsonrpc":"2.0","id":2,"error":{"code":-1,"message":"no tools"}}'
    )
    res = ex.run_smoke(_manifest(), _sv(), run_container=_fake_runner(run=(0, out, "")))
    assert res["failure_reason"] == "tools_list_failed"


def test_runtime_timeout_is_review(enabled):
    res = ex.run_smoke(
        _manifest(),
        _sv(),
        run_container=_fake_runner(run_exc=subprocess.TimeoutExpired("x", 1)),
    )
    assert res["failure_reason"] == "timeout"


def test_volume_is_always_removed(enabled):
    seen = []

    def runner(argv, input_text, timeout):
        seen.append(argv)
        if len(seen) == 1:
            return (1, "", "boom")  # install fails
        return (0, "", "")

    ex.run_smoke(_manifest(), _sv(), run_container=runner)
    # last call must be the volume rm even though install failed
    assert any(
        a[:3] == [ex.CONTAINER_RUNTIME or "docker", "volume", "rm"] for a in seen
    )


def test_skipped_credentialed_returns_skipped_result(enabled):
    res = ex.run_smoke(_manifest(env_keys=["K"]), _sv(), run_container=_fake_runner())
    assert res["status"] == "skipped" and res["review_reason"] == "credentialed"


def test_precondition_returns_none(enabled):
    res = ex.run_smoke(
        _manifest(), _sv(exists=False, version=None), run_container=_fake_runner()
    )
    assert res is None  # nothing recorded; pre-smoke gate handles it


# --- PyPI (2c-3) argv + success ---------------------------------------------


def test_install_argv_pypi_uv_pip_target_as_root():
    argv = ex.install_argv_pypi("img", "vol", "mcp-server-time", "2026.7.10")
    assert argv[argv.index("--network") + 1] == "default"
    assert "vol:/app:rw" in argv
    assert argv[argv.index("--user") + 1] == "0:0"  # fresh volume is root-owned
    assert "--cap-drop=ALL" in argv
    joined = " ".join(argv)
    assert "uv pip install --target /app mcp-server-time==2026.7.10" in joined


def test_run_argv_pypi_console_script_nonroot_netnone_ro():
    argv = ex.run_argv_pypi("img", "vol", "mcp-server-time", "2026.7.10")
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv
    assert "vol:/app:ro" in argv
    assert argv[argv.index("--user") + 1] == "1000:1000"
    assert "PYTHONPATH=/app" in argv
    assert "/app/bin/mcp-server-time" in argv  # console script, faithful to uvx <pkg>


def test_pypi_success_maps_to_passed_console_script(enabled):
    res = ex.run_smoke(
        _pypi_manifest(),
        _pypi_sv(),
        run_container=_fake_runner(run=(0, _passing_stdout(), "")),
    )
    assert res["status"] == "passed"
    assert res["runtime"] == "pypi"
    assert res["run_model"] == "console_script"
    assert res["package"] == "mcp-server-time"
    assert res["version"] == "2026.7.10"
    assert res["tools_count"] == 1
    assert res["command_hash"] and res["image_digest"]


def test_pypi_startup_crash_is_hard(enabled):
    res = ex.run_smoke(
        _pypi_manifest(), _pypi_sv(), run_container=_fake_runner(run=(1, "boom", ""))
    )
    assert res["status"] == "failed" and res["failure_reason"] == "startup_crash"


def test_pypi_install_fail_is_review_fallback(enabled):
    res = ex.run_smoke(
        _pypi_manifest(),
        _pypi_sv(),
        run_container=_fake_runner(install=(1, "", "boom")),
    )
    assert res["failure_reason"] == "install_failed"


def test_pypi_initialize_fail_is_review(enabled):
    out = '{"jsonrpc":"2.0","id":1,"error":{"code":-1,"message":"no"}}'
    res = ex.run_smoke(
        _pypi_manifest(), _pypi_sv(), run_container=_fake_runner(run=(0, out, ""))
    )
    assert res["failure_reason"] == "initialize_failed"


def test_pypi_tools_list_fail_after_init_is_hard(enabled):
    out = (
        '{"jsonrpc":"2.0","id":1,"result":{}}\n'
        '{"jsonrpc":"2.0","id":2,"error":{"code":-1,"message":"no tools"}}'
    )
    res = ex.run_smoke(
        _pypi_manifest(), _pypi_sv(), run_container=_fake_runner(run=(0, out, ""))
    )
    assert res["failure_reason"] == "tools_list_failed"
