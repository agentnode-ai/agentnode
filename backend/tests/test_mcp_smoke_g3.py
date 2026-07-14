"""G3 — host-resource preflight, bounded in-flight slots, bounded subprocess output.

Pure/mock + real-subprocess tests (no Docker, no DB). The DB-backed resource-block
evidence path is exercised in test_mcp_smoke_recovery_db-style suites; here we cover
the measurement gate, the overwrite-safety rule, the in-flight counter + scheduling
wrapper, and the byte-bounded runtime reader (incl. a single giant line).
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from app.mcp import smoke_executor as ex

_MOCK = str(Path(__file__).parent / "_mcp_mock_server.py")


@pytest.fixture(autouse=True)
def _reset():
    ex._inflight = 0
    yield
    ex._inflight = 0


# ---------------------------------------------------------------------------
# check_host_resources — measurement + thresholds + fail-closed
# ---------------------------------------------------------------------------


def _patch_measures(monkeypatch, mem, disk, load=0.5):
    monkeypatch.setattr(ex, "_read_mem_available_mb", lambda: mem)
    monkeypatch.setattr(ex, "_free_disk_gb", lambda p: disk)
    # os.getloadavg is absent on Windows -> raising=False so the test runs cross-platform.
    monkeypatch.setattr(ex.os, "getloadavg", lambda: (load, load, load), raising=False)


def test_resources_ok_above_thresholds(monkeypatch):
    monkeypatch.setattr(ex.settings, "MCP_SMOKE_MIN_AVAILABLE_MEMORY_MB", 1024)
    monkeypatch.setattr(ex.settings, "MCP_SMOKE_MIN_FREE_DISK_GB", 5)
    _patch_measures(monkeypatch, mem=2048, disk=20.0, load=3.0)
    r = ex.check_host_resources()
    assert r.ok is True and r.reason is None
    assert r.memory_available_mb == 2048 and r.disk_free_gb == 20.0
    assert r.load_1m == 3.0  # high load is diagnostic only — never blocks


def test_resources_memory_below(monkeypatch):
    monkeypatch.setattr(ex.settings, "MCP_SMOKE_MIN_AVAILABLE_MEMORY_MB", 1024)
    _patch_measures(monkeypatch, mem=512, disk=20.0)
    r = ex.check_host_resources()
    assert r.ok is False and r.reason == "memory_below_minimum"


def test_resources_disk_below(monkeypatch):
    monkeypatch.setattr(ex.settings, "MCP_SMOKE_MIN_FREE_DISK_GB", 5)
    _patch_measures(monkeypatch, mem=4096, disk=1.0)
    r = ex.check_host_resources()
    assert r.ok is False and r.reason == "disk_below_minimum"


def test_resources_memory_measurement_failed_is_fail_closed(monkeypatch):
    _patch_measures(monkeypatch, mem=None, disk=20.0)
    r = ex.check_host_resources()
    assert r.ok is False and r.reason == "memory_measurement_failed"


def test_resources_disk_measurement_failed_is_fail_closed(monkeypatch):
    _patch_measures(monkeypatch, mem=4096, disk=None)
    r = ex.check_host_resources()
    assert r.ok is False and r.reason == "disk_measurement_failed"


def test_read_mem_available_parses_proc_meminfo(monkeypatch):
    fake = io.StringIO(
        "MemTotal:        3911580 kB\nMemFree: 100 kB\nMemAvailable:    2097152 kB\n"
    )
    monkeypatch.setattr("builtins.open", lambda *a, **k: fake)
    assert ex._read_mem_available_mb() == 2048  # 2097152 kB -> 2048 MiB


# ---------------------------------------------------------------------------
# _should_store_resource_unavailable — never downgrade authoritative evidence
# ---------------------------------------------------------------------------


def test_overwrite_no_existing_smoke_stores():
    assert ex._should_store_resource_unavailable(None, "none") is True


def test_overwrite_fresh_pass_is_protected():
    prev = {"status": "passed"}
    assert ex._should_store_resource_unavailable(prev, "fresh") is False


def test_overwrite_hard_fail_is_protected():
    prev = {"status": "failed", "failure_reason": "tools_list_failed"}
    assert ex._should_store_resource_unavailable(prev, "not_passed") is False
    prev2 = {"status": "failed", "failure_reason": "startup_crash"}
    assert ex._should_store_resource_unavailable(prev2, "not_passed") is False
    prev3 = {"status": "failed", "failure_reason": "protocol_error"}
    assert ex._should_store_resource_unavailable(prev3, "not_passed") is False


def test_overwrite_transient_fail_is_replaced():
    prev = {"status": "failed", "failure_reason": "timeout"}
    assert ex._should_store_resource_unavailable(prev, "not_passed") is True


def test_overwrite_expired_or_key_mismatch_pass_is_replaced():
    prev = {"status": "passed"}
    assert ex._should_store_resource_unavailable(prev, "expired") is True
    assert ex._should_store_resource_unavailable(prev, "key_mismatch") is True


def test_overwrite_unavailable_or_skipped_is_replaced():
    assert ex._should_store_resource_unavailable({"status": "unavailable"}, "x") is True
    assert ex._should_store_resource_unavailable({"status": "skipped"}, "x") is True


# ---------------------------------------------------------------------------
# In-flight counter + scheduling wrapper
# ---------------------------------------------------------------------------


class _BG:
    def __init__(self):
        self.tasks = []

    def add_task(self, fn, *a):
        self.tasks.append((fn, a))


class _BGFail:
    def add_task(self, fn, *a):
        raise RuntimeError("scheduling boom")


def _patch_schedulable(monkeypatch):
    monkeypatch.setattr(ex, "smoke_availability", lambda: (True, ""))
    monkeypatch.setattr(ex, "should_smoke", lambda m, sv: (True, None, None))
    monkeypatch.setattr(ex, "should_schedule_smoke_recheck", lambda m, sv: True)


def test_claim_release_and_max(monkeypatch):
    monkeypatch.setattr(ex.settings, "MCP_SMOKE_MAX_PENDING", 1)  # max inflight = 2
    assert ex._try_claim_inflight() is True and ex._inflight == 1
    assert ex._try_claim_inflight() is True and ex._inflight == 2
    assert ex._try_claim_inflight() is False and ex._inflight == 2  # busy
    ex._release_inflight()
    assert ex._inflight == 1


def test_release_never_negative():
    ex._inflight = 0
    ex._release_inflight()
    assert ex._inflight == 0


def test_schedule_claims_and_adds_wrapper(monkeypatch):
    monkeypatch.setattr(ex.settings, "MCP_SMOKE_MAX_PENDING", 1)
    _patch_schedulable(monkeypatch)
    bg = _BG()
    assert ex.maybe_schedule_smoke(bg, "sub-1", {}, {}) is True
    assert ex._inflight == 1
    assert len(bg.tasks) == 1
    assert bg.tasks[0][0] is ex._run_scheduled_smoke  # the releasing wrapper


def test_schedule_busy_when_full(monkeypatch):
    monkeypatch.setattr(ex.settings, "MCP_SMOKE_MAX_PENDING", 1)
    _patch_schedulable(monkeypatch)
    ex._inflight = 2  # already at max
    bg = _BG()
    assert ex.maybe_schedule_smoke(bg, "sub-2", {}, {}) is False
    assert ex._inflight == 2 and bg.tasks == []  # no task, counter unchanged


def test_schedule_releases_slot_if_add_task_fails(monkeypatch):
    monkeypatch.setattr(ex.settings, "MCP_SMOKE_MAX_PENDING", 1)
    _patch_schedulable(monkeypatch)
    with pytest.raises(RuntimeError):
        ex.maybe_schedule_smoke(_BGFail(), "sub-3", {}, {})
    assert ex._inflight == 0  # slot freed on scheduling failure


@pytest.mark.asyncio
async def test_wrapper_releases_on_success(monkeypatch):
    async def fake(_id):
        return None

    monkeypatch.setattr(ex, "run_and_store_smoke", fake)
    ex._inflight = 1
    await ex._run_scheduled_smoke("x")
    assert ex._inflight == 0


@pytest.mark.asyncio
async def test_wrapper_releases_on_exception(monkeypatch):
    async def boom(_id):
        raise RuntimeError("task boom")

    monkeypatch.setattr(ex, "run_and_store_smoke", boom)
    ex._inflight = 1
    with pytest.raises(RuntimeError):
        await ex._run_scheduled_smoke("x")
    assert ex._inflight == 0


# ---------------------------------------------------------------------------
# _do_handshake excessive_output mapping (fake recv)
# ---------------------------------------------------------------------------


def _recv_from(items):
    it = iter(items)

    def recv(_timeout):
        try:
            return next(it)
        except StopIteration:
            raise ex._Timeout

    return recv


def test_handshake_excessive_output_at_initialize():
    res = ex._do_handshake(
        lambda m: None, _recv_from([ex._EXCESSIVE_OUTPUT]), step_timeout=1
    )
    assert res["failure_reason"] == "excessive_output"
    assert res["protocol_stage"] == "initialize_excessive_output"


def test_handshake_excessive_output_at_tools_list():
    init_ok = '{"jsonrpc":"2.0","id":1,"result":{}}'
    res = ex._do_handshake(
        lambda m: None,
        _recv_from([init_ok, ex._EXCESSIVE_OUTPUT]),
        step_timeout=1,
    )
    assert res["failure_reason"] == "excessive_output"
    assert res["protocol_stage"] == "tools_list_excessive_output"
    assert res["initialized"] is True


def test_excessive_output_is_transient_review():
    from app.mcp.smoke import TRANSIENT_FAILURES, derive_smoke_evidence

    assert "excessive_output" in TRANSIENT_FAILURES
    ev = derive_smoke_evidence(
        {"status": "failed", "failure_reason": "excessive_output"}
    )
    assert (
        ev["passed"] is False and ev["future"] is True and ev["review_fallback"] is True
    )


# ---------------------------------------------------------------------------
# Real bounded reader (subprocess) — flood + single giant line + no thread leak
# ---------------------------------------------------------------------------


def test_reader_flood_is_bounded_excessive_output(monkeypatch):
    monkeypatch.setattr(ex.settings, "MCP_SMOKE_MAX_OUTPUT_BYTES", 4096)
    res = ex._run_handshake([sys.executable, _MOCK, "flood"], 5)
    assert res["failure_reason"] == "excessive_output"


def test_reader_single_giant_line_is_bounded(monkeypatch):
    # A 2 MB line with NO newline must trip the byte cap (a line-based reader would
    # buffer the whole line first). MAX_OUTPUT_BYTES small so it trips fast.
    monkeypatch.setattr(ex.settings, "MCP_SMOKE_MAX_OUTPUT_BYTES", 4096)
    res = ex._run_handshake([sys.executable, _MOCK, "giant_line"], 5)
    assert res["failure_reason"] == "excessive_output"


def test_stdioproc_close_ends_reader_no_leak():
    proc = subprocess.Popen(
        [sys.executable, _MOCK, "flood"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    sp = ex._StdioProc(proc, 4096)
    got = None
    for _ in range(200):
        line = sp.recv_line(2)
        if line is ex._EXCESSIVE_OUTPUT:
            got = line
            break
    assert got is ex._EXCESSIVE_OUTPUT
    sp.close()
    assert not sp._reader.is_alive()  # reader thread ended -> no leak


def test_normal_handshake_still_green_regression():
    # The bounded/binary reader must not break the deterministic happy path.
    res = ex._run_handshake([sys.executable, _MOCK, "normal"], 5)
    assert res["ok"] is True and res["tools_count"] == 2


# ---------------------------------------------------------------------------
# Install output is not captured into RAM (capture=False on the seam)
# ---------------------------------------------------------------------------


def test_install_and_volume_rm_run_without_capture(monkeypatch):
    monkeypatch.setattr(ex, "smoke_availability", lambda: (True, ""))
    seen = []

    def seam(argv, input_text, timeout, capture=True):
        seen.append(capture)
        return (0, "", "")

    def hs_ok(argv, timeout):
        return {
            "ok": True,
            "initialized": True,
            "tools_count": 2,
            "protocol_stage": "ok",
            "failure_reason": None,
        }

    manifest = {
        "runtime": "mcp",
        "package_id": "t",
        "mcp_server": {"pypi_package": "mcp-server-time", "command": ["uvx", "x==1"]},
    }
    sv = {
        "registry": "pypi",
        "package_name": "mcp-server-time",
        "package_exists": True,
        "resolved_version": "2026.7.10",
        "command_pinning": "pinned",
    }
    res = ex.run_smoke(manifest, sv, run_container=seam, handshake=hs_ok)
    assert res["status"] == "passed"
    # both seam calls (install + volume-rm) must be capture=False (no RAM capture)
    assert seen and all(c is False for c in seen)
