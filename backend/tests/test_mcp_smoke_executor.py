"""Slice 2c-2/2c-3 — sandbox-smoke executor (npm + PyPI, inert by default).

No real network / registry / container runs here: the single `run_container`
seam is injected, and availability is toggled via settings. Tests cover the
fail-closed availability, the should_smoke gating (npm + pypi), the pure
argv/JSON-RPC/parse helpers (npm `npx --offline` + pypi `uv pip install --target`
/ console script), and the full status mapping (pass / hard-block /
review-fallback) for both registries.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from app.mcp import smoke_executor as ex


@pytest.fixture(autouse=True)
def _recovery_ready():
    # G2: smoke_availability() now also gates on the process-local recovery status.
    # Default it to "ready" so these existing tests reach the runtime/image checks;
    # the dedicated recovery-gate tests live in test_mcp_smoke_reaper.py.
    ex._set_recovery_status("ready")
    yield
    ex._set_recovery_status("ready")


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


# --- full status mapping via injected seams (run_container + handshake) -------


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(ex.settings, "MCP_SMOKE_MODE", "container")
    monkeypatch.setattr(ex, "CONTAINER_RUNTIME", "docker")
    # image present
    monkeypatch.setattr(ex, "smoke_availability", lambda: (True, ""))


def _run_seam(install=(0, "", ""), install_exc=None):
    """run_container fake: phase-1 install (1st call) then volume-rm (later)."""
    state = {"n": 0}

    def runner(argv, input_text, timeout):
        state["n"] += 1
        if state["n"] == 1:
            if install_exc:
                raise install_exc
            return install
        return (0, "", "")  # volume rm

    return runner


def _hs_never(argv, timeout):
    raise AssertionError("handshake must not run (phase 2 should be unreached)")


def _hs_pass(tools_count=1):
    return lambda argv, timeout: {
        "ok": True,
        "initialized": True,
        "tools_count": tools_count,
        "protocol_stage": "ok",
        "failure_reason": None,
    }


def _hs_fail(failure_reason, protocol_stage=None):
    return lambda argv, timeout: {
        "ok": False,
        "initialized": False,
        "tools_count": None,
        "protocol_stage": protocol_stage,
        "failure_reason": failure_reason,
    }


def test_success_maps_to_passed(enabled):
    res = ex.run_smoke(
        _manifest(), _sv(), run_container=_run_seam(), handshake=_hs_pass(1)
    )
    assert res["status"] == "passed"
    assert res["tools_count"] == 1
    assert res["initialized"] is True
    assert res["protocol_stage"] == "ok"
    assert res["runtime"] == "npm" and res["version"] == "1.2.3"
    assert res["command_hash"] and res["image_digest"]
    # 2c-4a: freshness stamps
    assert res["checked_at"] and res["expires_at"] and res["recheck_at"]
    assert res["schema_version"] == ex.settings.MCP_SMOKE_SCHEMA_VERSION


def test_install_fail_is_review_fallback(enabled):
    res = ex.run_smoke(
        _manifest(),
        _sv(),
        run_container=_run_seam(install=(1, "", "boom")),
        handshake=_hs_never,
    )
    assert res["status"] == "failed" and res["failure_reason"] == "install_failed"


def test_install_timeout_is_review_fallback(enabled):
    res = ex.run_smoke(
        _manifest(),
        _sv(),
        run_container=_run_seam(install_exc=subprocess.TimeoutExpired("x", 1)),
        handshake=_hs_never,
    )
    assert res["failure_reason"] == "timeout"


def test_startup_crash_is_hard(enabled):
    res = ex.run_smoke(
        _manifest(),
        _sv(),
        run_container=_run_seam(),
        handshake=_hs_fail("startup_crash", "process_exited_before_initialize"),
    )
    assert res["status"] == "failed" and res["failure_reason"] == "startup_crash"


def test_protocol_error_is_hard(enabled):
    res = ex.run_smoke(
        _manifest(),
        _sv(),
        run_container=_run_seam(),
        handshake=_hs_fail("protocol_error", "initialize_malformed"),
    )
    assert res["failure_reason"] == "protocol_error"


def test_initialize_fail_is_review(enabled):
    res = ex.run_smoke(
        _manifest(),
        _sv(),
        run_container=_run_seam(),
        handshake=_hs_fail("initialize_failed", "initialize_error"),
    )
    assert res["failure_reason"] == "initialize_failed"


def test_tools_list_error_is_hard(enabled):
    # A genuine JSON-RPC error response to tools/list -> hard block.
    res = ex.run_smoke(
        _manifest(),
        _sv(),
        run_container=_run_seam(),
        handshake=_hs_fail("tools_list_failed", "tools_list_error"),
    )
    assert res["failure_reason"] == "tools_list_failed"


def test_tools_list_no_response_is_transient_not_hard(enabled):
    # 2c-6 FIX: a server exiting after initialize (the prod race symptom) -> a
    # TRANSIENT timeout/review, NOT a hard tools_list_failed.
    res = ex.run_smoke(
        _manifest(),
        _sv(),
        run_container=_run_seam(),
        handshake=_hs_fail("timeout", "tools_list_no_response"),
    )
    assert res["failure_reason"] == "timeout"  # transient/review, not hard
    assert res["protocol_stage"] == "tools_list_no_response"


def test_runtime_timeout_is_review(enabled):
    res = ex.run_smoke(
        _manifest(),
        _sv(),
        run_container=_run_seam(),
        handshake=_hs_fail("timeout", "tools_list_timeout"),
    )
    assert res["failure_reason"] == "timeout"


def test_volume_is_always_removed(enabled):
    seen = []

    def runner(argv, input_text, timeout):
        seen.append(argv)
        if len(seen) == 1:
            return (1, "", "boom")  # install fails
        return (0, "", "")

    ex.run_smoke(_manifest(), _sv(), run_container=runner, handshake=_hs_never)
    # last call must be the volume rm even though install failed
    assert any(
        a[:3] == [ex.CONTAINER_RUNTIME or "docker", "volume", "rm"] for a in seen
    )


def test_skipped_credentialed_returns_skipped_result(enabled):
    res = ex.run_smoke(
        _manifest(env_keys=["K"]), _sv(), run_container=_run_seam(), handshake=_hs_never
    )
    assert res["status"] == "skipped" and res["review_reason"] == "credentialed"


def test_precondition_returns_none(enabled):
    res = ex.run_smoke(
        _manifest(),
        _sv(exists=False, version=None),
        run_container=_run_seam(),
        handshake=_hs_never,
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
        _pypi_manifest(), _pypi_sv(), run_container=_run_seam(), handshake=_hs_pass(2)
    )
    assert res["status"] == "passed"
    assert res["runtime"] == "pypi"
    assert res["run_model"] == "console_script"
    assert res["package"] == "mcp-server-time"
    assert res["version"] == "2026.7.10"
    assert res["tools_count"] == 2
    assert res["protocol_stage"] == "ok"
    assert res["command_hash"] and res["image_digest"]


def test_pypi_startup_crash_is_hard(enabled):
    res = ex.run_smoke(
        _pypi_manifest(),
        _pypi_sv(),
        run_container=_run_seam(),
        handshake=_hs_fail("startup_crash", "process_exited_before_initialize"),
    )
    assert res["status"] == "failed" and res["failure_reason"] == "startup_crash"


def test_pypi_install_fail_is_review_fallback(enabled):
    res = ex.run_smoke(
        _pypi_manifest(),
        _pypi_sv(),
        run_container=_run_seam(install=(1, "", "boom")),
        handshake=_hs_never,
    )
    assert res["failure_reason"] == "install_failed"


def test_pypi_initialize_fail_is_review(enabled):
    res = ex.run_smoke(
        _pypi_manifest(),
        _pypi_sv(),
        run_container=_run_seam(),
        handshake=_hs_fail("initialize_failed", "initialize_error"),
    )
    assert res["failure_reason"] == "initialize_failed"


def test_pypi_tools_list_error_is_hard(enabled):
    res = ex.run_smoke(
        _pypi_manifest(),
        _pypi_sv(),
        run_container=_run_seam(),
        handshake=_hs_fail("tools_list_failed", "tools_list_error"),
    )
    assert res["failure_reason"] == "tools_list_failed"


# --- 2c-4a: current_smoke_keys + expiry stamp -------------------------------


def test_current_smoke_keys_npm():
    keys = ex.current_smoke_keys(_manifest(), _sv())
    assert keys["runtime"] == "npm"
    assert keys["package"] == "@scope/mcp"
    assert keys["version"] == "1.2.3"
    assert keys["run_model"] == "npx_offline"
    assert keys["image_digest"] == ex.settings.MCP_SMOKE_IMAGE
    assert keys["schema_version"] == ex.settings.MCP_SMOKE_SCHEMA_VERSION
    assert keys["command_hash"]  # hash of the manifest command


def test_current_smoke_keys_pypi():
    keys = ex.current_smoke_keys(_pypi_manifest(), _pypi_sv())
    assert keys["runtime"] == "pypi"
    assert keys["package"] == "mcp-server-time"
    assert keys["run_model"] == "console_script"


def test_result_expires_is_checked_plus_ttl():
    from datetime import datetime

    r = ex._result("passed", checked_at="2026-07-13T00:00:00+00:00")
    exp = datetime.fromisoformat(r["expires_at"])
    chk = datetime.fromisoformat(r["checked_at"])
    assert (exp - chk).days == ex.settings.MCP_SMOKE_TTL_DAYS
    assert r["recheck_at"] == r["expires_at"]
    assert r["schema_version"] == ex.settings.MCP_SMOKE_SCHEMA_VERSION


# --- 2c-4b: recheck triggers (freshness-gated scheduling) --------------------


def _now():
    return datetime.now(timezone.utc)


def _fresh_passed_sv():
    sv = _sv()
    keys = ex.current_smoke_keys(_manifest(), sv)
    sv["smoke"] = {
        **keys,
        "status": "passed",
        "checked_at": _now().isoformat(),
        "expires_at": (_now() + timedelta(days=20)).isoformat(),
    }
    return sv


def _expired_passed_sv():
    sv = _sv()
    keys = ex.current_smoke_keys(_manifest(), sv)
    sv["smoke"] = {
        **keys,
        "status": "passed",
        "expires_at": (_now() - timedelta(days=1)).isoformat(),
    }
    return sv


def test_recheck_skip_when_fresh():
    assert ex.should_schedule_smoke_recheck(_manifest(), _fresh_passed_sv()) is False


def test_recheck_scheduled_when_expired():
    assert ex.should_schedule_smoke_recheck(_manifest(), _expired_passed_sv()) is True


def test_recheck_scheduled_when_key_mismatch():
    sv = _fresh_passed_sv()
    sv["smoke"]["command_hash"] = "changed"
    assert ex.should_schedule_smoke_recheck(_manifest(), sv) is True


def test_recheck_scheduled_when_no_result():
    assert ex.should_schedule_smoke_recheck(_manifest(), _sv()) is True


def test_recheck_skip_when_running_fresh():
    sv = _sv()
    sv["smoke_running"] = {"started_at": _now().isoformat()}
    assert ex.should_schedule_smoke_recheck(_manifest(), sv) is False


def test_recheck_scheduled_when_running_stale():
    sv = _sv()
    sv["smoke_running"] = {"started_at": (_now() - timedelta(hours=1)).isoformat()}
    assert ex.should_schedule_smoke_recheck(_manifest(), sv) is True


# --- transient protection: _should_overwrite_smoke ---------------------------


def test_overwrite_fresh_pass_not_clobbered_by_unavailable():
    assert ex._should_overwrite_smoke("fresh", {"status": "unavailable"}) is False


def test_overwrite_fresh_pass_not_clobbered_by_transient_fail():
    assert (
        ex._should_overwrite_smoke(
            "fresh", {"status": "failed", "failure_reason": "install_failed"}
        )
        is False
    )


def test_overwrite_fresh_pass_by_new_pass():
    assert ex._should_overwrite_smoke("fresh", {"status": "passed"}) is True


def test_overwrite_fresh_pass_by_hard_failure():
    # A definitive hard failure DOES overwrite a fresh pass (the server broke).
    assert (
        ex._should_overwrite_smoke(
            "fresh", {"status": "failed", "failure_reason": "startup_crash"}
        )
        is True
    )


def test_overwrite_when_previous_not_fresh():
    assert ex._should_overwrite_smoke("expired", {"status": "unavailable"}) is True
    assert ex._should_overwrite_smoke("key_mismatch", {"status": "unavailable"}) is True


# --- maybe_schedule_smoke end-to-end (fake BackgroundTasks) ------------------


class _BG:
    def __init__(self):
        self.tasks = []

    def add_task(self, fn, *a):
        self.tasks.append((fn, a))


def test_schedule_disabled_no_task(monkeypatch):
    monkeypatch.setattr(ex.settings, "MCP_SMOKE_MODE", "disabled")
    bg = _BG()
    assert ex.maybe_schedule_smoke(bg, "id", _manifest(), _sv()) is False
    assert bg.tasks == []


def test_schedule_fresh_no_task(enabled):
    bg = _BG()
    assert ex.maybe_schedule_smoke(bg, "id", _manifest(), _fresh_passed_sv()) is False
    assert bg.tasks == []


def test_schedule_expired_creates_task(enabled):
    bg = _BG()
    assert ex.maybe_schedule_smoke(bg, "id", _manifest(), _expired_passed_sv()) is True
    assert len(bg.tasks) == 1


def test_schedule_no_result_creates_task(enabled):
    bg = _BG()
    assert ex.maybe_schedule_smoke(bg, "id", _manifest(), _sv()) is True
    assert len(bg.tasks) == 1


def test_schedule_credentialed_no_task(enabled):
    bg = _BG()
    assert ex.maybe_schedule_smoke(bg, "id", _manifest(env_keys=["K"]), _sv()) is False
    assert bg.tasks == []


# --- 2c-6: deterministic handshake state machine (pure, fake send/recv) -------

_INIT_OK = '{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05"}}'
_INIT_ERR = '{"jsonrpc":"2.0","id":1,"error":{"code":-1,"message":"no"}}'
_TOOLS_OK = '{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"a"},{"name":"b"}]}}'
_TOOLS_ERR = '{"jsonrpc":"2.0","id":2,"error":{"code":-1,"message":"no tools"}}'
_TOOLS_NOTOOLS = '{"jsonrpc":"2.0","id":2,"result":{"nope":true}}'
_NOTIF = '{"jsonrpc":"2.0","method":"notifications/message"}'
_UNKNOWN_ID = '{"jsonrpc":"2.0","id":99,"result":{}}'
_TIMEOUT = "__TIMEOUT__"


def _mk_recv(items):
    """items: list of str line / ex._EOF / _TIMEOUT (raises _Timeout)."""
    it = iter(items)

    def recv(timeout):
        try:
            item = next(it)
        except StopIteration:
            return ex._EOF
        if item == _TIMEOUT:
            raise ex._Timeout
        return item

    return recv


def _do(items):
    sent = []
    res = ex._do_handshake(lambda m: sent.append(m), _mk_recv(items), step_timeout=1)
    return res, sent


def test_hs_normal_success():
    res, sent = _do([_INIT_OK, _TOOLS_OK])
    assert (
        res["ok"] is True and res["tools_count"] == 2 and res["protocol_stage"] == "ok"
    )
    # sequential: initialize sent first, then initialized + tools/list AFTER the
    # initialize response was received.
    assert sent[0]["method"] == "initialize"
    methods = [m.get("method") for m in sent]
    assert "notifications/initialized" in methods and "tools/list" in methods


def test_hs_skips_notification_and_log_noise_before_responses():
    res, _ = _do([_NOTIF, "starting up (not json)", _INIT_OK, _NOTIF, _TOOLS_OK])
    assert res["ok"] is True and res["tools_count"] == 2


def test_hs_skips_unknown_response_id():
    res, _ = _do([_INIT_OK, _UNKNOWN_ID, _TOOLS_OK])
    assert res["ok"] is True


def test_hs_process_exit_before_initialize_is_startup_crash():
    res, _ = _do([ex._EOF])
    assert res["protocol_stage"] == "process_exited_before_initialize"
    assert res["failure_reason"] == "startup_crash"


def test_hs_initialize_timeout():
    res, _ = _do([_TIMEOUT])
    assert (
        res["protocol_stage"] == "initialize_timeout"
        and res["failure_reason"] == "timeout"
    )


def test_hs_initialize_error():
    res, _ = _do([_INIT_ERR])
    assert res["protocol_stage"] == "initialize_error"
    assert res["failure_reason"] == "initialize_failed"


def test_hs_initialize_malformed():
    res, _ = _do(["{not valid json"])
    assert res["protocol_stage"] == "initialize_malformed"
    assert res["failure_reason"] == "protocol_error"


def test_hs_tools_list_no_response_after_init_is_transient():
    # THE regression at the state-machine level: EOF after initialize -> transient.
    res, _ = _do([_INIT_OK, ex._EOF])
    assert res["initialized"] is True
    assert res["protocol_stage"] == "tools_list_no_response"
    assert res["failure_reason"] == "timeout"  # NOT tools_list_failed


def test_hs_tools_list_timeout_is_transient():
    res, _ = _do([_INIT_OK, _TIMEOUT])
    assert (
        res["protocol_stage"] == "tools_list_timeout"
        and res["failure_reason"] == "timeout"
    )


def test_hs_tools_list_error_is_hard():
    res, _ = _do([_INIT_OK, _TOOLS_ERR])
    assert res["protocol_stage"] == "tools_list_error"
    assert res["failure_reason"] == "tools_list_failed"


def test_hs_tools_list_malformed_shape():
    res, _ = _do([_INIT_OK, _TOOLS_NOTOOLS])
    assert res["protocol_stage"] == "tools_list_malformed"
    assert res["failure_reason"] == "protocol_error"


# --- 2c-6: real-subprocess integration (a REAL interactive mock MCP server) ---
# No docker/image needed: exercises the real Popen + threaded reader + handshake.

_MOCK = os.path.join(os.path.dirname(__file__), "_mcp_mock_server.py")


def _rh(mode, timeout=5):
    return ex._run_handshake([sys.executable, _MOCK, mode], timeout)


def test_integration_normal_handshake_passes():
    r = _rh("normal")
    assert r["ok"] is True and r["tools_count"] == 2 and r["protocol_stage"] == "ok"


def test_integration_exit_after_init_is_transient_regression():
    # Reproduces the production race: the server answers initialize then exits
    # before tools/list. The OLD send-all-then-EOF runner classified this as a
    # HARD tools_list_failed; the deterministic handshake classifies it as
    # tools_list_no_response -> timeout (transient/review), never a hard block.
    r = _rh("exit_after_init")
    assert r["ok"] is False
    assert r["initialized"] is True
    assert r["protocol_stage"] == "tools_list_no_response"
    assert r["failure_reason"] == "timeout"


def test_integration_crash_is_startup_crash():
    r = _rh("crash")
    assert r["failure_reason"] == "startup_crash"


def test_integration_tools_error_is_hard():
    r = _rh("tools_error")
    assert r["failure_reason"] == "tools_list_failed"
    assert r["protocol_stage"] == "tools_list_error"


def test_integration_init_error():
    r = _rh("init_error")
    assert r["failure_reason"] == "initialize_failed"


def test_integration_noise_before_responses_is_skipped():
    r = _rh("noise")
    assert r["ok"] is True and r["tools_count"] == 2


def test_integration_hang_init_times_out():
    r = _rh("hang_init", timeout=1)  # short per-step timeout
    assert r["failure_reason"] == "timeout"
    assert r["protocol_stage"] == "initialize_timeout"
