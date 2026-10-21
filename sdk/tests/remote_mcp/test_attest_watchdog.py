"""The independent watchdog's attestation checks, the renewal timer's unit policy and the rollback
preflight (attest-renewal C12, C14, C18; Codex D8, D10; counter-checks 16, 17, 27-30, 32-35)."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from agentnode_sdk.remote_mcp import target_ops as O
from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.remote_mcp import trust_timers as TT
from agentnode_sdk.remote_mcp import trust_watchdog as W
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout

from .test_trust_schedule import SHIPPED
from .test_trust_watchdog import Stand, _codes, _show

H = 3600


@pytest.fixture
def s(tmp_path):
    return Stand(tmp_path)


def _left(reg, tid, hours):
    import copy

    def m(doc, now):
        a = copy.deepcopy(doc["targets"][tid]["attestation"])
        a["expires_at"] = now + hours * H
        a["measured_at"] = a["expires_at"] - T.ATTESTATION_MAX_SECONDS[a["assurance"]] + 60
        a["record_digest"] = T.digest({k: a[k] for k in sorted(a) if k != "record_digest"})
        doc["targets"][tid]["attestation"] = a
        return "test"
    reg.change(m, actor="t")
    rec = reg.load().target(tid).attestation
    _state_set(reg, tid, published_record_digest=rec["record_digest"])   # as the renewal records it


def _state_set(reg, tid=None, **fields):
    path = O.renew_state_path(reg.stand)
    doc = json.loads(path.read_text())
    if tid is None:
        doc.update(fields)
    else:
        doc["targets"].setdefault(tid, {}).update(fields)
    O._write_private(path, doc)


def test_bootstrapped_state_is_green(s):
    """D8: right after install (bootstrap, no run yet) nothing is missing."""
    assert s.check()["state"] == "green"


def test_reserve_warning_boundary_is_48_hours(s):
    """C13/C14: at more than 48 h left green, at 48 h red (inclusive)."""
    _left(s.reg, "managed-w1", 48.05)
    assert "attestation_reserve_low" not in _codes(s.check())
    _left(s.reg, "managed-w1", 47.99)
    assert "attestation_reserve_low" in _codes(s.check())


def test_an_expired_record_is_red(s):
    _left(s.reg, "managed-w1", 0.0003)
    time.sleep(1.5)
    assert "attestation_invalid" in _codes(s.check())


@pytest.mark.parametrize("result", ["degraded", "blocked", "busy"])
def test_a_degraded_or_blocked_renewal_stays_red_every_run(s, result):
    """CC29 / negative: every hourly run while it lasts; an alert file existing changes nothing."""
    _state_set(s.reg, "managed-w1", result=result, reason="x")
    for _ in range(3):
        report = s.check()
        assert report["state"] == "red" and ("renew_%s" % result) in _codes(report)
    _state_set(s.reg, "managed-w1", result="renewed", reason="")
    assert s.check()["state"] == "green"


def test_a_record_the_renewal_did_not_publish_is_inconsistent(s):
    _state_set(s.reg, "managed-w1", published_record_digest="0" * 64)
    assert "register_inconsistent" in _codes(s.check())


def test_missing_or_unreadable_state_is_red(s):
    p = O.renew_state_path(s.reg.stand)
    p.write_text("{not json")
    assert "renew_state_unreadable" in _codes(s.check())
    p.unlink()
    assert "renew_state_missing" in _codes(s.check())


def test_an_active_target_missing_from_the_state_is_red(s):
    path = O.renew_state_path(s.reg.stand)
    doc = json.loads(path.read_text())
    doc["targets"] = {}
    O._write_private(path, doc)
    assert "renew_state_missing" in _codes(s.check())


def test_an_overdue_renewal_run_is_red(s):
    now = time.time()
    _state_set(s.reg, last_run={"finished_at": now - W.RENEW_RUN_MAX_AGE_SECONDS + 60, "status": "ok"})
    assert "renew_run_overdue" not in _codes(s.check(now=now))
    _state_set(s.reg, last_run={"finished_at": now - W.RENEW_RUN_MAX_AGE_SECONDS - 60, "status": "ok"})
    assert "renew_run_overdue" in _codes(s.check(now=now))


@pytest.mark.parametrize("renew_next", [None, "past", "far"])
def test_the_renewal_timer_must_have_a_finite_next_within_its_bound(s, renew_next):
    """CC28: a missing, past or too-distant NEXT of the renewal timer is red, whatever the refresh says."""
    now = time.time()
    value = {None: None, "past": int((now - 5) * 1e6), "far": int((now + W.RENEW_BOUND_SECONDS + 60) * 1e6)}[renew_next]
    report = W.check(s.stand, now=now, run=_show(int((now + 3600) * 1e6), renew_usec=value), tls=s.tls,
                     state_file=s.state)
    assert report["state"] == "red" and any(W.RENEW_TIMER in p for p in report["problems"])


def test_a_suspended_default_is_red(s):
    s.reg.block("managed-w1", actor="t", why="test")
    assert "default_target_not_active" in _codes(s.check())


def _open_op(s, age, kind="renew"):
    d = Path(s.reg.stand) / "targets" / "ops"
    d.mkdir(parents=True, exist_ok=True)
    op = {"op_id": "op-1-abcd", "kind": kind, "target_id": "managed-w1", "params": {}, "steps": {},
          "state": "open", "created_at": time.time() - age, "error": ""}
    (d / "op-1-abcd.json").write_text(json.dumps(op))


@pytest.mark.parametrize("kind", ["renew", "probe"])
def test_an_operation_open_for_30_minutes_is_hung(s, kind):
    _open_op(s, O.HUNG_AFTER_SECONDS - 60, kind)
    assert "probe_hung" not in _codes(s.check())
    _open_op(s, O.HUNG_AFTER_SECONDS + 60, kind)
    report = s.check()
    assert "probe_hung" in _codes(report)


def test_the_watchdog_never_takes_a_lock(s, monkeypatch):
    """CC30 / negative: inspection cannot take, release or delay a probe's lock (no ProcessLock at all)."""
    from agentnode_sdk.gateway import filelock

    def forbidden(self):
        raise AssertionError("the watchdog took a lock")
    monkeypatch.setattr(filelock.ProcessLock, "__enter__", forbidden)
    _open_op(s, O.HUNG_AFTER_SECONDS + 60)
    report = s.check()
    assert "probe_hung" in _codes(report) and "watchdog_error" not in _codes(report)


@pytest.mark.skipif(not Path("/proc/locks").exists(), reason="the kernel's lock table is a Linux file")
def test_the_watchdog_reads_a_held_lock_from_the_kernel(s):
    """Lock holders from /proc/locks: held while a probe holds it, not held after."""
    from agentnode_sdk.gateway.filelock import ProcessLock
    _open_op(s, O.HUNG_AFTER_SECONDS + 60)
    lock = Path(s.reg.stand) / "targets" / (O.PROBE_LOCK_PREFIX + "managed-w1")
    ready, release = threading.Event(), threading.Event()

    def hold():
        with ProcessLock(lock, timeout=5):
            ready.set()
            release.wait(20)
    th = threading.Thread(target=hold)
    th.start()
    try:
        assert ready.wait(10)
        assert any("lock held" in p for p in s.check()["problems"])
    finally:
        release.set()
        th.join(10)
    assert any("lock not held" in p for p in s.check()["problems"])


# ---------------------------------------------------------------------- C12: the renewal units
def test_the_shipped_renewal_units_satisfy_the_policy():
    assert TT.unit_problems(TT.RENEW_TIMER, SHIPPED[TT.RENEW_TIMER]) == []
    assert TT.unit_problems(TT.RENEW_SERVICE, SHIPPED[TT.RENEW_SERVICE]) == []
    spec = dict(TT.parse_unit(SHIPPED[TT.RENEW_TIMER])["Timer"])["OnCalendar"]
    assert TT.calendar_elapses(spec) == [(3, 30, 0), (9, 30, 0), (15, 30, 0), (21, 30, 0)]


def test_the_renewal_timer_needs_persistent():
    """CC16."""
    found = TT.unit_problems(TT.RENEW_TIMER, SHIPPED[TT.RENEW_TIMER].replace("Persistent=true", "Persistent=false"))
    assert any("Persistent=true missing" in p for p in found)


@pytest.mark.parametrize("line", ["OnBootSec=5min", "OnUnitActiveSec=6h", "OnActiveSec=1h", "OnStartupSec=1h",
                                  "OnUnitInactiveSec=6h"])
def test_the_renewal_timer_refuses_a_monotonic_trigger(line):
    """CC17."""
    text = SHIPPED[TT.RENEW_TIMER].replace("Persistent=true", "Persistent=true\n" + line)
    assert any("monotonic trigger" in p for p in TT.unit_problems(TT.RENEW_TIMER, text))


def test_the_renewal_service_must_alert_end_and_run_the_renewal():
    svc = SHIPPED[TT.RENEW_SERVICE]
    assert any("OnFailure" in p for p in TT.unit_problems(TT.RENEW_SERVICE, svc.replace("OnFailure=", "X-Off=")))
    assert any("TimeoutStartSec" in p for p in TT.unit_problems(TT.RENEW_SERVICE, svc.replace("TimeoutStartSec=", "X-T=")))
    assert any("attest-renew" in p for p in TT.unit_problems(TT.RENEW_SERVICE, svc.replace("attest-renew", "show")))


@pytest.mark.parametrize("dep", ["Requires=rmcp-attest-renew.service", "After=rmcp-attest-renew.timer",
                                 "Wants=rmcp-attest-renew.service", "PartOf=rmcp-attest-renew.timer"])
def test_the_watchdog_never_depends_on_the_renewal(dep):
    """CC27."""
    text = SHIPPED[TT.WATCHDOG_SERVICE].replace("After=network-online.target", "After=network-online.target\n" + dep)
    assert any("depends on what it watches" in p for p in TT.unit_problems(TT.WATCHDOG_SERVICE, text))
    assert "rmcp-attest-renew" not in SHIPPED[TT.WATCHDOG_SERVICE] + SHIPPED[TT.WATCHDOG_TIMER]


# ---------------------------------------------------------------------- C18: the rollback preflight
def _quiet_systemd(calls):
    def run(argv):
        calls.append(list(argv))
        if argv[1] == "show":
            return 0, "LoadState=not-found\nActiveState=inactive\n"
        return 0, ""
    return run


def test_preflight_passes_when_quiescent(s):
    calls = []
    assert TT.quiesce(stand=str(s.stand), trust_check=s.trust_refusals, run=_quiet_systemd(calls), wait_seconds=0) == []
    assert ["systemctl", "disable", "--now", TT.RENEW_TIMER, TT.CYCLE_TIMER, TT.REFRESH_TIMER] == calls[0]   # scheduling stopped first


def test_preflight_refuses_an_open_head_only_operation(s):
    """CC32: a36ddcdd cannot resume a `renew` operation -- none may be open across the rollback."""
    _open_op(s, 5, "renew")
    found = TT.quiesce(stand=str(s.stand), trust_check=s.trust_refusals, run=_quiet_systemd([]), wait_seconds=0)
    assert any("open renew operation" in p for p in found)


def test_preflight_refuses_below_48_hours_on_the_default(s):
    """CC35."""
    _left(s.reg, "managed-w1", 47.9)
    found = TT.quiesce(stand=str(s.stand), trust_check=s.trust_refusals, run=_quiet_systemd([]), wait_seconds=0)
    assert any("below 48 h" in p for p in found)
    _left(s.reg, "managed-w1", 48.1)
    assert TT.quiesce(stand=str(s.stand), trust_check=s.trust_refusals, run=_quiet_systemd([]), wait_seconds=0) == []


def test_preflight_waits_for_a_running_renewal_then_refuses(s):
    clock = {"t": 0.0}

    def run(argv):
        return (0, "ActiveState=active\n") if argv[1] == "show" else (0, "")
    found = TT.quiesce(stand=str(s.stand), trust_check=s.trust_refusals, run=run, now=lambda: clock["t"],
                       sleep=lambda x: clock.__setitem__("t", clock["t"] + x), wait_seconds=60)
    assert any("still running" in p for p in found) and clock["t"] >= 60


@pytest.mark.skipif(not Path("/proc/locks").exists(), reason="the kernel's lock table is a Linux file")
def test_preflight_refuses_a_held_probe_lock(s):
    from agentnode_sdk.gateway.filelock import ProcessLock
    lock = Path(s.reg.stand) / "targets" / (O.PROBE_LOCK_PREFIX + "managed-w1")
    ready, release = threading.Event(), threading.Event()

    def hold():
        with ProcessLock(lock, timeout=5):
            ready.set()
            release.wait(20)
    th = threading.Thread(target=hold)
    th.start()
    try:
        assert ready.wait(10)
        assert any("lock held" in p for p in TT.quiesce(stand=str(s.stand), trust_check=s.trust_refusals, run=_quiet_systemd([]), wait_seconds=0))
    finally:
        release.set()
        th.join(10)


def test_remove_refuses_when_not_quiescent_and_never_unlinks_a_lock(s, tmp_path):
    """CC33 / CC34: nothing removed without quiescence; lock files and the register are never touched."""
    root = tmp_path / "root"
    lock = Path(str(Path(s.reg.stand) / "targets" / (O.PROBE_LOCK_PREFIX + "managed-w1")) + ".lock")
    lock.write_text("")
    before = {p: p.read_bytes() for p in (Path(s.reg.stand) / "targets").rglob("*") if p.is_file()}
    _open_op(s, 5, "renew")
    done = TT.remove(root=root, run=_quiet_systemd([]), stand=str(s.stand), wait_seconds=0)
    assert done["removed"] is False and any(x.startswith("PREFLIGHT") for x in done["left"])
    (Path(s.reg.stand) / "targets" / "ops" / "op-1-abcd.json").unlink()
    done = TT.remove(root=root, run=_quiet_systemd([]), stand=str(s.stand), wait_seconds=0)
    assert lock.exists()
    after = {p: p.read_bytes() for p in (Path(s.reg.stand) / "targets").rglob("*") if p.is_file()}
    for p, data in before.items():
        if "ops" not in p.parts and p.name != "clock.json":
            assert after.get(p) == data, p                    # register, history, head, acks: untouched
    if Path("/proc/locks").exists():
        assert done["removed"] is True and not O.renew_state_path(s.reg.stand).exists()   # operational state gone
    else:                                                      # no lock table: a lock nobody can judge refuses
        assert done["removed"] is False and any("lock state unknown" in x for x in done["left"])
