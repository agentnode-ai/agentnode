"""FINDING-TFL-WD-1: a watchdog that tells healthy, briefly busy and broken apart (profile trust-watchdog-r1,
Codex Q-TWD-0001 D1-D9, DECISION-0001 R1-R9).

The live a2b045ec watchdog was red in 17 of 24 hourly runs while admission never failed: (1) its timer and the
5-minute cycle start in the same second, and systemd shows no next elapse for a timer whose service is still
running; (2) a 13-hour ack age left over from the 6-hourly push; (3) "never" until the first cycle after an
install. Real issuer, lists, register and signed acknowledgements; systemd is a stand-in that answers per unit
as systemd 257 does (`Systemd`), and the monotonic clock and boot id are injected. Each test names the frozen
criterion (Cn), counter-check (CCn) or negative it holds."""
from __future__ import annotations

import json
import os
import socket
import ssl
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentnode_sdk.pki import revocation as rl
from agentnode_sdk.remote_mcp import target_router as TR
from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.remote_mcp import trust_publication as TP
from agentnode_sdk.remote_mcp import trust_timers as TT
from agentnode_sdk.remote_mcp import trust_watchdog as W
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout

from .test_target_lifecycle import _provision, env  # noqa: F401 - the lifecycle stand
from .test_trust_watchdog import Stand, _codes

MONO = 500_000.0                                              # this boot's monotonic "now", seconds
BOOT = "boot-aaaa"
ROLE_OF = {W.REFRESH_TIMER: "refresh", W.RENEW_TIMER: "renew", W.CYCLE_TIMER: "cycle", W.FLOOR_TIMER: "floor",
           W.WATCHDOG_TIMER: "watchdog"}
BOUND_OF = {W.REFRESH_TIMER: W.REFRESH_BOUND_SECONDS, W.RENEW_TIMER: W.RENEW_BOUND_SECONDS,
            W.CYCLE_TIMER: W.FIVE_MINUTE_BOUND_SECONDS, W.FLOOR_TIMER: W.FIVE_MINUTE_BOUND_SECONDS,
            W.WATCHDOG_TIMER: W.WATCHDOG_BOUND_SECONDS}
# the owner's numbers, not the module's: TimeoutStartSec + 60 s
OWNER_RUN_BOUND = {"cycle": 300, "floor": 120, "refresh": 1260, "renew": 1860, "watchdog": 180}


def _us(seconds_mono: float) -> int:
    return int(round(seconds_mono * 1_000_000))


class Systemd:
    """systemd 257 as `systemctl show` and `list-timers --all -o json` answer, per unit. `on_read[(unit, n)]`
    runs before the n-th `show` of that unit is answered: the world may change between two reads."""

    def __init__(self, now: float):
        self.timers: dict[str, dict] = {}
        self.services: dict[str, dict] = {}
        self.reads: dict[str, int] = {}
        self.on_read: dict[tuple, object] = {}
        for unit, ahead in ((W.REFRESH_TIMER, 3600), (W.RENEW_TIMER, 3600), (W.CYCLE_TIMER, 60),
                            (W.FLOOR_TIMER, 60), (W.WATCHDOG_TIMER, 1800)):
            self.timer(unit, next_usec=int((now + ahead) * 1e6))
            self.service(unit[:-6] + ".service")

    def timer(self, unit, *, next_usec="keep", load="loaded", active="active", file="enabled", triggers="default",
              activated="keep"):
        t = self.timers.setdefault(unit, {"next": None, "activated": _us(MONO - 86400)})
        if next_usec != "keep":
            t["next"] = next_usec
        t.update(load=load, active=active, file=file,
                 triggers=(unit[:-6] + ".service") if triggers == "default" else triggers)
        if activated != "keep":
            t["activated"] = activated
        return t

    def service(self, unit, *, active="inactive", start=0, exit_=0, status="0", result="success"):
        self.services[unit] = {"ActiveState": active, "SubState": "start" if active == "activating" else "dead",
                               "ExecMainStartTimestampMonotonic": start, "ExecMainExitTimestampMonotonic": exit_,
                               "ExecMainStatus": status, "Result": result}
        return self.services[unit]

    def run(self, argv):
        if argv[1] == "show":
            unit = argv[2]
            self.reads[unit] = self.reads.get(unit, 0) + 1
            hook = self.on_read.get((unit, self.reads[unit]))
            if hook:
                hook()
            if unit in self.timers:
                t = self.timers[unit]
                return ("LoadState=%s\nActiveState=%s\nUnitFileState=%s\nNextElapseUSecMonotonic=%s\nTriggers=%s\n"
                        "ActiveEnterTimestampMonotonic=%s\n" % (t["load"], t["active"], t["file"],
                                                                "1" if t["next"] else "infinity", t["triggers"],
                                                                t["activated"]))
            if unit in self.services:
                return "".join("%s=%s\n" % kv for kv in self.services[unit].items())
            return "LoadState=not-found\nActiveState=inactive\nUnitFileState=\n"
        unit = argv[-1]
        t = self.timers.get(unit)
        return json.dumps([{"next": t["next"], "unit": unit}] if t and t["load"] == "loaded" else [])


@pytest.fixture
def s(tmp_path):
    return Stand(tmp_path)


def _cycle_state(s, *, at=None, mono=None, boot=BOOT, reach="all-ok", reach_at=None, reach_mono=None, last=None):
    """What a `targets_cli trust-cycle` of the head leaves. `at=None` = never succeeded."""
    live = ["managed-w1"]
    if reach == "all-ok":
        ra = reach_at if reach_at is not None else at
        rm = reach_mono if reach_mono is not None else mono
        reach = {tid: {"ok_at": ra, "ok_mono": rm, "boot_id": boot, "failing_since": None, "code": "",
                       "via": "preflight"} for tid in live}
    doc = {"schema": TP.CYCLE_STATE_SCHEMA, "last_success_at": at, "last_success_mono": mono, "boot_id": boot,
           "last_run": last or {"finished_at": at, "status": "ok", "entry": "cycle"}, "reach": reach}
    path = TP.cycle_state_path(s.stand)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))
    return path


def _check(s, sd, *, now=None, mono=MONO, boot=BOOT, **kw):
    now = time.time() if now is None else now
    return W.check(s.stand, now=now, run=sd.run, tls=s.tls, state_file=s.state, monotonic=lambda: mono,
                   boot_id=lambda: boot, **kw)


def _healthy(s, now):
    sd = Systemd(now)
    _cycle_state(s, at=now - 30, mono=MONO - 30)
    return sd


def _alerts(s) -> Path:
    return s.state.parent / W.ALERTS_LOG_NAME


# ====================================================================== C2-C5: timers
def test_positive_control_head_is_green(s):
    now = time.time()
    report = _check(s, _healthy(s, now), now=now)
    assert report["state"] == "green" and report["problems"] == [] and report["pending"] == []


@pytest.mark.parametrize("unit", sorted(ROLE_OF))
def test_a_finite_next_is_green_for_every_timer(unit):
    """C2: the finite-NEXT path is unchanged."""
    now = time.time()
    sd = Systemd(now)
    found, note, _ = W.judge_timer(unit, role=ROLE_OF[unit], now=now, bound_seconds=BOUND_OF[unit], run=sd.run,
                                   monotonic=lambda: MONO)
    assert found == [] and note is None


@pytest.mark.parametrize("unit", sorted(ROLE_OF))
@pytest.mark.parametrize("fault,code", [
    ({"load": "not-found"}, "timer_not_loaded"), ({"file": "disabled"}, "timer_not_enabled"),
    ({"active": "inactive"}, "timer_not_active"), ({"next_usec": "past"}, "timer_next_in_past"),
    ({"next_usec": "far"}, "timer_next_too_far"), ({"next_usec": "malformed"}, "timer_next_not_finite")])
def test_every_broken_timer_keeps_its_named_red(unit, fault, code):
    """C2 / negatives: missing, disabled, inactive, past, too far, malformed -- for all five timers, and a running
    service explains none of them."""
    now = time.time()
    sd = Systemd(now)
    f = dict(fault)
    if f.get("next_usec") == "past":
        f["next_usec"] = int((now - 1) * 1e6)
    elif f.get("next_usec") == "far":
        f["next_usec"] = int((now + BOUND_OF[unit] + 60) * 1e6)
    elif f.get("next_usec") == "malformed":
        f["next_usec"] = "soon"
    sd.timer(unit, **f)
    sd.service(unit[:-6] + ".service", active="activating", start=_us(MONO - 5))
    found, note, _ = W.judge_timer(unit, role=ROLE_OF[unit], now=now, bound_seconds=BOUND_OF[unit], run=sd.run,
                                   monotonic=lambda: MONO)
    assert code in {p.split()[0] for p in found} and note is None


@pytest.mark.parametrize("unit", sorted(ROLE_OF))
def test_a_running_service_explains_no_next_up_to_its_bound(unit):
    """C3 / CC1 / CC5: no next elapse while the triggered service runs: green through the bound (inclusive), one
    second over is `timer_service_overrunning`."""
    now = time.time()
    role = ROLE_OF[unit]
    assert W.RUN_BOUND_SECONDS[role] == OWNER_RUN_BOUND[role]
    sd = Systemd(now)
    sd.timer(unit, next_usec=None)
    svc = unit[:-6] + ".service"
    for age, want in ((0, "green"), (OWNER_RUN_BOUND[role], "green"), (OWNER_RUN_BOUND[role] + 1, "over")):
        sd.service(svc, active="activating", start=_us(MONO - age))
        found, note, _ = W.judge_timer(unit, role=role, now=now, bound_seconds=BOUND_OF[unit], run=sd.run,
                                       monotonic=lambda: MONO)
        if want == "green":
            assert found == [] and note.startswith("timer_running %s (%s " % (unit, svc)), (age, found)
        else:
            assert [p.split()[0] for p in found] == ["timer_service_overrunning"] and note is None


@pytest.mark.parametrize("active", ["active", "deactivating"])
def test_active_and_deactivating_count_as_running(active):
    now = time.time()
    sd = Systemd(now)
    sd.timer(W.CYCLE_TIMER, next_usec=None)
    sd.service("rmcp-trust-cycle.service", active=active, start=_us(MONO - 20))
    found, note, _ = W.judge_timer(W.CYCLE_TIMER, role="cycle", now=now, bound_seconds=330, run=sd.run,
                                   monotonic=lambda: MONO)
    assert found == [] and note


@pytest.mark.parametrize("svc", [
    {"active": "inactive", "start": 0}, {"active": "failed", "start": 0, "result": "exit-code", "status": "1"},
    {"active": "activating", "start": 0}, {"active": "activating", "start": "garbage"},
    {"active": "activating", "start": "infinity"}, {"active": "activating", "start": "future"}])
def test_no_next_without_a_believable_running_service_is_red(svc):
    """C3 / CC2 / CC4 / negatives: inactive, failed, a zero/malformed/infinite/future start -- `timer_next_not_finite`."""
    now = time.time()
    sd = Systemd(now)
    sd.timer(W.CYCLE_TIMER, next_usec=None)
    kw = dict(svc)
    if kw.get("start") == "future":
        kw["start"] = _us(MONO + 30)
    sd.service("rmcp-trust-cycle.service", **kw)
    found, note, _ = W.judge_timer(W.CYCLE_TIMER, role="cycle", now=now, bound_seconds=330, run=sd.run,
                                   monotonic=lambda: MONO)
    assert [p.split()[0] for p in found] == ["timer_next_not_finite"] and note is None


def test_an_unexpected_triggered_service_is_named(s):
    """R1 / CC3: only `<name>.service` explains `<name>.timer`; a timer triggering anything else stays red."""
    now = time.time()
    sd = Systemd(now)
    sd.timer(W.CYCLE_TIMER, next_usec=None, triggers="rmcp-twd-proof-other.service")
    sd.service("rmcp-twd-proof-other.service", active="activating", start=_us(MONO - 5))
    sd.service("rmcp-trust-cycle.service", active="activating", start=_us(MONO - 5))
    found, note, _ = W.judge_timer(W.CYCLE_TIMER, role="cycle", now=now, bound_seconds=330, run=sd.run,
                                   monotonic=lambda: MONO)
    assert {p.split()[0] for p in found} == {"timer_next_not_finite", "timer_trigger_unexpected"} and note is None
    sd.timer(W.CYCLE_TIMER, triggers="")
    found, note, _ = W.judge_timer(W.CYCLE_TIMER, role="cycle", now=now, bound_seconds=330, run=sd.run,
                                   monotonic=lambda: MONO)
    assert [p.split()[0] for p in found] == ["timer_next_not_finite"]


def test_a_run_that_ends_between_the_reads_is_judged_on_one_re_read():
    """C4 / CC6: the service finished between the timer read and the service read: a re-read with a valid finite
    next elapse is green; a re-read without one stays `timer_next_not_finite`."""
    now = time.time()
    sd = Systemd(now)
    sd.timer(W.CYCLE_TIMER, next_usec=None)
    sd.service("rmcp-trust-cycle.service", active="inactive", exit_=_us(MONO - 1))
    sd.on_read[("rmcp-trust-cycle.service", 1)] = lambda: sd.timer(W.CYCLE_TIMER, next_usec=int((now + 290) * 1e6))
    found, note, st = W.judge_timer(W.CYCLE_TIMER, role="cycle", now=now, bound_seconds=330, run=sd.run,
                                    monotonic=lambda: MONO)
    assert found == [] and W.next_elapse(st) is not None
    sd2 = Systemd(now)
    sd2.timer(W.CYCLE_TIMER, next_usec=None)
    found, _note, _ = W.judge_timer(W.CYCLE_TIMER, role="cycle", now=now, bound_seconds=330, run=sd2.run,
                                    monotonic=lambda: MONO)
    assert [p.split()[0] for p in found] == ["timer_next_not_finite"] and sd2.reads[W.CYCLE_TIMER] == 2
    sd3 = Systemd(now)
    sd3.timer(W.CYCLE_TIMER, next_usec=None)
    sd3.on_read[("rmcp-trust-cycle.service", 1)] = lambda: sd3.timer(W.CYCLE_TIMER, next_usec=int((now - 5) * 1e6))
    found, _note, _ = W.judge_timer(W.CYCLE_TIMER, role="cycle", now=now, bound_seconds=330, run=sd3.run,
                                    monotonic=lambda: MONO)
    assert [p.split()[0] for p in found] == ["timer_next_in_past"]


def test_the_live_collision_at_minute_20_is_green_end_to_end(s):
    """C3 / the live finding (1): at :20 the cycle and the floor run started in the same second as the watchdog
    (journal 2026-10-22, 10 of 17 red runs) -- both timers without a next elapse, both services 1 s into their
    run. The head is green and says why; nothing is pending."""
    now = time.time()
    sd = _healthy(s, now)
    for unit in (W.CYCLE_TIMER, W.FLOOR_TIMER):
        sd.timer(unit, next_usec=None)
        sd.service(unit[:-6] + ".service", active="activating", start=_us(MONO - 1))
    report = _check(s, sd, now=now)
    assert report["state"] == "green", report["problems"]
    assert sorted(n.split()[1] for n in report["facts"]["timer_running"]) == sorted([W.CYCLE_TIMER, W.FLOOR_TIMER])


def test_a_hung_cycle_under_the_same_collision_is_red(s):
    """C3: the same picture, but the cycle has been running 301 s -- red, named."""
    now = time.time()
    sd = _healthy(s, now)
    sd.timer(W.CYCLE_TIMER, next_usec=None)
    sd.service("rmcp-trust-cycle.service", active="activating", start=_us(MONO - 301))
    assert "timer_service_overrunning" in _codes(_check(s, sd, now=now))


def test_the_timer_check_cli_uses_the_running_service(monkeypatch, capsys):
    """F3 caller: the cert-check drop-in's `timer-check` of the watchdog timer while the watchdog runs (the CLI
    reads the real monotonic clock, so the stand-in service started on it)."""
    now = time.time()
    sd = Systemd(now)
    sd.timer(W.WATCHDOG_TIMER, next_usec=None)
    monkeypatch.setattr(W, "timer_state", lambda unit, run=None, _t=W.timer_state: _t(unit, run=sd.run))
    monkeypatch.setattr(W, "service_state", lambda unit, run=None, _s=W.service_state: _s(unit, run=sd.run))
    sd.service("rmcp-trust-watchdog.service", active="activating", start=_us(time.monotonic() - 3))
    assert W.main(["timer-check", "--unit", W.WATCHDOG_TIMER, "--bound-seconds", "3900"]) == 0
    assert "timer_running rmcp-trust-watchdog.timer" in capsys.readouterr().out
    sd.service("rmcp-trust-watchdog.service", active="activating", start=_us(time.monotonic() - 181))
    assert W.main(["timer-check", "--unit", W.WATCHDOG_TIMER, "--bound-seconds", "3900"]) == 1
    assert "ALERT timer_service_overrunning" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        W.main(["timer-check", "--unit", "rmcp-twd-proof-x.timer", "--bound-seconds", "90"])
    assert W.main(["timer-check", "--unit", "rmcp-twd-proof-x.timer", "--bound-seconds", "90", "--role", "cycle"]) == 1


def test_trust_timers_check_uses_the_running_service():
    """F3 caller: the installer's check of all five timers (real monotonic clock)."""
    now = time.time()
    sd = Systemd(now)
    sd.timer(W.CYCLE_TIMER, next_usec=None)
    sd.service("rmcp-trust-cycle.service", active="activating", start=_us(time.monotonic() - 2))
    assert TT.check(run=lambda argv: (0, sd.run(argv)), now=now) == []
    sd.timer(W.FLOOR_TIMER, next_usec=None)
    assert [p.split()[0] for p in TT.check(run=lambda argv: (0, sd.run(argv)), now=now)] == ["timer_next_not_finite"]


# ---------------------------------------------------------------------- C5: the service timeout policy
def _shipped(name):
    src = Path(__file__).resolve().parents[2] / "deploy" / "remote-mcp" / name
    return src.read_text(encoding="utf-8")


def test_every_shipped_service_is_bounded_below_its_run_bound():
    """C5: each service a timer judgement may find running ends within RUN_BOUND - 60; the watchdog has 2min."""
    for name in TT.SERVICE_ROLES:
        assert TT.unit_problems(name, _shipped(name)) == [], name
    assert "TimeoutStartSec=2min" in _shipped(TT.WATCHDOG_SERVICE).splitlines()


@pytest.mark.parametrize("name,value,ok", [
    (TT.WATCHDOG_SERVICE, None, False), (TT.WATCHDOG_SERVICE, "infinity", False), (TT.WATCHDOG_SERVICE, "0", False),
    (TT.WATCHDOG_SERVICE, "121", False), (TT.WATCHDOG_SERVICE, "120", True), (TT.CYCLE_SERVICE, "241", False),
    (TT.CYCLE_SERVICE, "4min", True), (TT.REFRESH_SERVICE, "1201", False), (TT.RENEW_SERVICE, "1801", False),
    (TT.FLOOR_SERVICE, "61", False)])
def test_a_missing_infinite_or_too_long_timeout_is_refused(name, value, ok):
    """C5 / CC7 / negatives: absent, infinite, zero, one second over RUN_BOUND - 60."""
    lines = [ln for ln in _shipped(name).splitlines() if not ln.startswith("TimeoutStartSec=")]
    if value is not None:
        lines.insert(lines.index("[Service]") + 1, "TimeoutStartSec=" + value)
    found = [p for p in TT.unit_problems(name, "\n".join(lines) + "\n") if "TimeoutStartSec" in p]
    assert (found == []) is ok, found


# ====================================================================== C6-C8: acknowledgements and rotation
@pytest.mark.parametrize("hours", [13, 23.9, 24])
def test_an_exact_current_ack_of_any_age_is_green(s, hours):
    """C6 / CC8: the live finding (2) -- an exact ack 13 to 24 h old with trust_current True is green."""
    now = time.time()
    s.ack("managed-w1", acked_at=now - hours * 3600)
    report = _check(s, _healthy(s, now), now=now)
    assert report["state"] == "green", report["problems"]
    assert T.trust_current(s.acks.load()["managed-w1"], now=now, deployment=s.facts())


@pytest.mark.parametrize("field,value", [("revocation_list_number", 99), ("revocation_list_sha256", "0" * 64),
                                         ("tombstones_sha256", "f" * 64), ("tombstones_not_after", 3.0)])
def test_a_wrong_ack_of_any_age_is_still_red(s, field, value):
    """C7 / negatives: a wrong number, digest or validity is `ack_mismatch`, fresh or old."""
    now = time.time()
    for age in (0, 20 * 3600):
        s.ack("managed-w1", acked_at=now - age, **{field: value})
        assert "ack_mismatch" in _codes(_check(s, _healthy(s, now), now=now))


def test_a_missing_or_malformed_ack_time_is_still_red(s):
    """C7 / negatives: `ack_time_missing` for a malformed ack; `ack_missing` without one."""
    now = time.time()
    p = Path(s.stand) / "targets" / "trust-acks.json"
    doc = json.loads(p.read_text())
    doc["acks"]["managed-w1"]["acked_at"] = "yesterday"
    doc.pop("signature")
    doc["signature"] = {"key_id": s.acks.key_id, "signature": s.op.sign(T._signed_bytes(
        {"schema": doc["schema"], "acks": doc["acks"]}, T._ACKS_DOMAIN)).hex()}
    p.write_bytes(T.canonical(doc) + b"\n")
    assert "ack_time_missing" in _codes(_check(s, _healthy(s, now), now=now))
    doc["acks"] = {}
    doc["signature"] = {"key_id": s.acks.key_id, "signature": s.op.sign(T._signed_bytes(
        {"schema": doc["schema"], "acks": {}}, T._ACKS_DOMAIN)).hex()}
    p.write_bytes(T.canonical(doc) + b"\n")
    assert "ack_missing" in _codes(_check(s, _healthy(s, now), now=now))


def test_an_expired_or_unsigned_pair_is_still_red(s):
    """C7 / negatives: an expired list and a forged list stay red whatever the acks say."""
    now = time.time()
    key, cert = s.world._ca()
    Path(s.tls.revocation_list).write_bytes(rl.build(key, cert, {}, number=rl.number_of(s.pair()[0]) + 1,
                                                     now=now - 8 * 86400))
    s.ack("managed-w1")
    assert "list_unusable" in _codes(_check(s, _healthy(s, now), now=now))
    fkey, fcert, _ = s.world.foreign_ca()
    Path(s.tls.revocation_list).write_bytes(rl.build(fkey, fcert, {}, number=99, now=now))
    assert "list_unusable" in _codes(_check(s, _healthy(s, now), now=now))


def test_an_inadmissible_predecessor_is_a_mismatch_even_inside_the_window(s):
    """C7 / CC9 / negatives: the target holds exactly the saved, verified predecessor, but the current pair's
    revocation content differs (a security publication 60 s ago): `ack_mismatch` at once, never `ack_previous`;
    and a content-equal predecessor whose saved generation no longer verifies is no predecessor either."""
    from .test_trust_floor_units import _record as _rec
    from .test_trust_floor_units import _save_generation
    now = time.time()
    prev = _save_generation(s)                                   # what the target holds, saved as the predecessor
    _rec(s, "r-000000000001", state=TP.DONE, phase="distributing", **{"from": prev})
    key, cert = s.world._ca()
    Path(s.tls.revocation_list).write_bytes(rl.build(key, cert, {"0abc01": now - 5},
                                                     number=rl.number_of(s.pair()[0]) + 1, now=now))
    cur = s.facts()
    assert cur["content_sha256"] != prev["content_sha256"]
    _rec(s, "r-000000000002", state=TP.DONE, phase="distributing", **{"from": prev, "result": cur},
         published_at=now - 60)
    report = _check(s, _healthy(s, now), now=now)
    assert "ack_mismatch" in _codes(report) and "managed-w1" not in report["facts"].get("ack_previous", [])
    assert not T.trust_current(s.acks.load()["managed-w1"], now=now, deployment=cur, previous=prev)


def test_an_unverifiable_predecessor_admits_nothing_inside_the_window(tmp_path):
    """C7 / negatives: content-equal rotation 60 s ago, but the saved generation was damaged: no `ack_previous`."""
    from .test_trust_floor_units import _record as _rec
    from .test_trust_floor_units import _save_generation
    s = Stand(tmp_path)
    now = time.time()
    prev = _save_generation(s)
    _rec(s, "r-000000000001", state=TP.DONE, phase="distributing", **{"from": prev})
    s.world.issuer.publish()
    _rec(s, "r-000000000002", state=TP.DONE, phase="distributing", **{"from": prev, "result": s.facts()},
         published_at=now - 60)
    (TP.generations_dir(s.stand) / "g-000000000001" / "revoked.crl").write_bytes(b"x")
    report = _check(s, _healthy(s, now), now=now)
    assert {"ack_mismatch", "previous_missing"} <= _codes(report)
    assert "managed-w1" not in report["facts"].get("ack_previous", [])


def _list_aged(s, age: float, now: float):
    """The current list re-signed with thisUpdate `age` seconds ago, and the target exactly on it."""
    key, cert = s.world._ca()
    Path(s.tls.revocation_list).write_bytes(rl.build(key, cert, {}, number=rl.number_of(s.pair()[0]) + 1,
                                                     now=now - age))
    s.ack("managed-w1")


def _record(s, rid, **fields):
    d = TP.records_dir(s.stand)
    d.mkdir(parents=True, exist_ok=True)
    rec = {"schema": TP.SCHEMA, "record_id": rid, "kind": TP.PUBLICATION, "reason": "rotation", "state": TP.DONE,
           "phase": "distributing", "created_at": time.time(), "from": None, "result": None, "targets": {}}
    rec.update(fields)
    (d / (rid + ".json")).write_text(json.dumps(rec))


def test_rotation_overdue_boundary_on_the_lists_thisupdate(s):
    """C8 / negatives: just before, exactly at and one second beyond `_rotate_after() + 900` (whole seconds: a
    CRL's thisUpdate has second precision)."""
    now = float(int(time.time()))
    due = TP._rotate_after() + 900
    for age, red in ((due - 1, False), (due, False), (due + 1, True)):
        _list_aged(s, age, now)
        codes = _codes(_check(s, _healthy(s, now), now=now))
        assert ("rotation_overdue" in codes) is red, (age, codes)


def test_rotation_overdue_reads_the_cycles_own_threshold(s, monkeypatch):
    """C8 / CC12: the cycle's `_rotate_after()` decides, never a second constant."""
    now = time.time()
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 3600.0)
    _list_aged(s, 3600 + 900 + 1, now)
    assert "rotation_overdue" in _codes(_check(s, _healthy(s, now), now=now))
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 7200.0)
    assert "rotation_overdue" not in _codes(_check(s, _healthy(s, now), now=now))


def test_rotation_age_is_the_lists_not_the_journals(s):
    """C8 / CC11: a record claiming a recent publication of the same pair does not make an old list young."""
    now = time.time()
    _list_aged(s, TP._rotate_after() + 901, now)
    _record(s, "r-000000000001", result=s.facts(), published_at=now - 60)
    assert "rotation_overdue" in _codes(_check(s, _healthy(s, now), now=now))


def test_a_lagging_target_or_an_open_publication_explains_a_late_rotation(s):
    """C8: with a target behind, the guard holds the rotation by design (the target is red on its own); an open
    publication record is the rotation under way."""
    now = time.time()
    _list_aged(s, TP._rotate_after() + 2000, now)
    s.ack("managed-w1", revocation_list_number=99)
    codes = _codes(_check(s, _healthy(s, now), now=now))
    assert "rotation_overdue" not in codes and "ack_mismatch" in codes
    s.ack("managed-w1")
    _record(s, "r-000000000001", state=TP.OPEN, phase="published", created_at=now - 30)
    codes = _codes(_check(s, _healthy(s, now), now=now))
    assert "rotation_overdue" not in codes


# ====================================================================== C9/C10: reachability
def test_reach_age_boundary_and_the_unreachable_report(s):
    """C10 / negatives: reached 900 s ago is green, 901 s red `target_unreachable` with classification, the first
    failure of the episode and the last success."""
    now = time.time()
    sd = Systemd(now)
    _cycle_state(s, at=now - 30, mono=MONO - 30, reach_at=now - 900, reach_mono=MONO - 900)
    assert _check(s, sd, now=now)["state"] == "green"
    reach = {"managed-w1": {"ok_at": now - 901, "ok_mono": MONO - 901, "boot_id": BOOT, "failing_since": now - 600,
                            "code": "target_offline", "via": "preflight"}}
    _cycle_state(s, at=now - 30, mono=MONO - 30, reach=reach)
    p = [x for x in _check(s, sd, now=now)["problems"] if x.startswith("target_unreachable")]
    assert p and "target_offline" in p[0] and W._iso(now - 600) in p[0] and W._iso(now - 901) in p[0]


def test_no_reach_entry_is_unknown_and_red_outside_initialization(s):
    """C10 / negatives: a live target without any reach (e.g. newly live) -- `target_reach_unknown`; a reach
    entry of a target that is no longer live is ignored."""
    now = time.time()
    sd = Systemd(now)
    _cycle_state(s, at=now - 30, mono=MONO - 30, reach={"gone-target": {"ok_at": now - 9999, "ok_mono": 1.0,
                                                                         "boot_id": BOOT, "code": "x"}})
    report = _check(s, sd, now=now)
    assert _codes(report) == {"target_reach_unknown"} and report["state"] == "red"


def test_reach_is_monotonic_within_a_boot(s):
    """C12 / CC21: a forward wall jump with the monotonic clock unchanged does not age a fresh reach or success."""
    now = time.time()
    sd = Systemd(now + 7200)
    _cycle_state(s, at=now - 30, mono=MONO - 30)                 # wall stamps from before the jump
    report = _check(s, sd, now=now + 7200)                       # the wall clock jumped 2 h, monotonic did not
    assert {"trust_cycle_not_succeeding", "target_unreachable"}.isdisjoint(_codes(report)), report["problems"]


def test_a_backward_wall_jump_never_refreshes_old_evidence(s):
    """C12 / CC22 / R3: across a boot (no monotonic comparison), evidence from the 'future' by more than 300 s is
    `clock_regressed`; within 300 s it counts as just now."""
    now = time.time()
    sd = Systemd(now)
    _cycle_state(s, at=now + 1000, mono=10.0, boot="boot-old")
    assert "clock_regressed" in _codes(_check(s, sd, now=now))
    _cycle_state(s, at=now + 200, mono=10.0, boot="boot-old")
    assert "clock_regressed" not in _codes(_check(s, sd, now=now))


# ---------------------------------------------------------------------- the cycle's side: observe_reach
def _reachable(env, tid="managed-w1", how="ok"):
    w = env["workers"][tid]

    def confirm(budget=None):
        if how == "ok":
            return None
        if how == "offline":
            raise ConnectionRefusedError("refused")
        if how == "identity":
            raise ssl.SSLCertVerificationError("not our anchor")
        if how == "protocol":
            raise RuntimeError("no-common-protocol")
        raise AssertionError(how)
    w.confirm_reachable = confirm
    return w


def _synced(env):
    report = env["ops"].refresh_trust()
    assert report["lagging"] == [], report
    return report


def _cycle(env):
    return env["ops"].refresh_trust(entry="cycle", distribute_wait=0.0, write_state=True)


@pytest.mark.parametrize("how,code", [("offline", "target_offline"), ("identity", "target_identity_mismatch"),
                                      ("protocol", "target_protocol_mismatch")])
def test_an_exact_target_is_reached_by_the_routers_preflight(env, how, code):
    """C9 / CC13: an exact target is not pushed; the cycle still reaches it -- the router's classification kept."""
    _synced(env)
    _reachable(env, how="ok")
    report = _cycle(env)
    assert report["pushed"] == {} and report["reach"]["managed-w1"] == {"ok": True, "via": "preflight", "code": ""}
    _reachable(env, how=how)
    report = _cycle(env)
    assert report["reach"]["managed-w1"] == {"ok": False, "via": "preflight", "code": code}


def test_trust_unavailable_material_is_classified_as_such(env):
    """C9: building the connection fails on THIS side's trust material -> `target_trust_unavailable`."""
    _synced(env)
    real = env["ops"].worker_for
    env["ops"].worker_for = lambda t: (_ for _ in ()).throw(RuntimeError("pair key unreadable"))
    try:
        assert TP.observe_reach(env["ops"], {"pushed": {}})["managed-w1"]["code"] == "target_trust_unavailable"
    finally:
        env["ops"].worker_for = real


def test_a_failed_preflight_never_changes_the_cycles_status(env):
    """C9 / CC14: the publication cycle's report, state status and exit semantics are those of the publication."""
    _synced(env)
    _reachable(env, how="ok")
    good = _cycle(env)
    _reachable(env, how="offline")
    bad = _cycle(env)
    for k in ("alerts", "lagging", "published", "deferred", "security"):
        assert good[k] == bad[k], k
    st = TP.load_cycle_state(env["reg"].stand)
    assert st["last_run"]["status"] == "ok" and st["reach"]["managed-w1"]["code"] == "target_offline"


def test_a_failed_push_is_not_a_reach(env):
    """C9 / CC16: a lagging target whose install_trust did not acknowledge is preflighted, not counted reached."""
    _synced(env)
    w = _reachable(env, how="offline")
    w.install_trust = lambda crl, tombs: (_ for _ in ()).throw(OSError("worker unreachable"))
    env["ops"].refresh_trust(force=True)
    report = _cycle(env)
    assert report["lagging"] == ["managed-w1"]
    assert report["reach"]["managed-w1"] == {"ok": False, "via": "preflight", "code": "target_offline"}


def test_an_exact_push_counts_as_a_reach(env, monkeypatch):
    """C9: the target this run pushed with an exact acknowledgement was reached (no preflight needed)."""
    _synced(env)
    _reachable(env, how="offline")                               # a preflight would fail: the push is what counts
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 1.0)
    time.sleep(1.2)
    report = _cycle(env)
    assert isinstance(report["pushed"].get("managed-w1"), int)
    assert report["reach"]["managed-w1"] == {"ok": True, "via": "push", "code": ""}


def test_two_targets_with_mixed_results(env):
    """negatives: two live targets -- one reached, one offline -- each recorded on its own."""
    _synced(env)
    tid = _provision(env)
    env["ops"].probe(tid)
    _synced(env)
    _reachable(env, "managed-w1", how="ok")
    _reachable(env, tid, how="offline")
    report = _cycle(env)
    assert report["reach"]["managed-w1"]["ok"] is True and report["reach"][tid]["code"] == "target_offline"


def test_the_failure_episode_keeps_its_first_failure():
    """C10 / CC15: two consecutive failures keep the FIRST `failing_since`; a success ends the episode; a cycle that
    observed nothing keeps what was there; a target no longer live is dropped."""
    m = TP._merge_reach
    a = m({}, {"t": {"ok": False, "via": "preflight", "code": "target_offline"}}, now=100.0, mono=1.0, boot=BOOT)
    b = m(a, {"t": {"ok": False, "via": "preflight", "code": "target_offline"}}, now=400.0, mono=301.0, boot=BOOT)
    assert a["t"]["failing_since"] == b["t"]["failing_since"] == 100.0
    c = m(b, {"t": {"ok": True, "via": "preflight", "code": ""}}, now=700.0, mono=601.0, boot=BOOT)
    assert c["t"] == {"ok_at": 700.0, "ok_mono": 601.0, "boot_id": BOOT, "failing_since": None, "code": "",
                      "via": "preflight"}
    assert m(c, None, now=900.0, mono=801.0, boot=BOOT) == c
    assert m(c, {}, now=900.0, mono=801.0, boot=BOOT) == {}


class _SlowWorker:
    def __init__(self, seconds):
        self.seconds = seconds

    def confirm_reachable(self, budget=None):
        time.sleep(self.seconds)


def _many(n, seconds):
    targets = [SimpleNamespace(target_id="t%02d" % i, endpoint_digest="e%d" % i, worker={}) for i in range(n)]
    ops = SimpleNamespace(register=None, acks=None, worker_for=lambda t: _SlowWorker(seconds))
    return targets, ops


def test_reach_runs_concurrently_within_one_deadline(monkeypatch):
    """C9 / CC17 / R2: 16 targets at 0.5 s each are all reached well inside a 2.5 s deadline (serialized they
    would take 8 s and most would be `reach_deadline`)."""
    targets, ops = _many(16, 0.5)
    monkeypatch.setattr(TP, "_live", lambda ops: targets)
    monkeypatch.setattr(TP, "REACH_DEADLINE_SECONDS", 2.5)
    started = time.monotonic()
    out = TP.observe_reach(ops, {"pushed": {}})
    assert all(v["ok"] for v in out.values()) and len(out) == 16, out
    assert time.monotonic() - started < 2.5


def test_reach_never_outlives_its_deadline(monkeypatch):
    """C9 / R2: a hanging handshake is `reach_deadline` and the cycle goes on at the deadline."""
    targets, ops = _many(2, 5.0)
    monkeypatch.setattr(TP, "_live", lambda ops: targets)
    monkeypatch.setattr(TP, "REACH_DEADLINE_SECONDS", 0.5)
    started = time.monotonic()
    out = TP.observe_reach(ops, {"pushed": {}})
    assert time.monotonic() - started < 2.0
    assert {v["code"] for v in out.values()} == {"reach_deadline"}
    assert TP.REACH_DEADLINE_SECONDS < 240 and TP.REACH_THREADS == 8 and TP.REACH_BUDGET_SECONDS == 10.0


HUNG_CHILD = r"""
import sys, threading, time
from types import SimpleNamespace
from agentnode_sdk.remote_mcp import trust_publication as TP

class Hung:
    def confirm_reachable(self, budget=None):
        threading.Event().wait()                     # a handshake that ignores its budget: never returns

targets = [SimpleNamespace(target_id="t%d" % i, endpoint_digest="e%d" % i, worker={}) for i in range(3)]
TP._live = lambda ops: targets
TP.REACH_DEADLINE_SECONDS = 1.0
out = TP.observe_reach(SimpleNamespace(register=None, acks=None, worker_for=lambda t: Hung()), {"pushed": {}})
print(sorted({v["code"] for v in out.values()}), flush=True)
"""


def test_a_hung_handshake_cannot_keep_the_cycle_process_alive(tmp_path):
    """C9 / F-TWD-R1-001: a preflight that never returns is `reach_deadline` at the deadline AND the process
    exits right after -- it is not held past the cycle's 240 s TimeoutStartSec by a worker it cannot stop."""
    import subprocess
    import sys
    script = tmp_path / "hung_child.py"
    script.write_text(HUNG_CHILD)
    sdk = str(Path(__file__).resolve().parents[2])
    started = time.monotonic()
    done = subprocess.run([sys.executable, str(script)], cwd=sdk, env=dict(os.environ, PYTHONPATH=sdk),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=60)
    assert done.returncode == 0 and "['reach_deadline']" in done.stdout, done.stdout
    assert time.monotonic() - started < 30


def test_a_failure_to_set_up_the_observation_is_an_observation(env, monkeypatch):
    """C9 / F-TWD-R1-001: the router or the threads cannot be set up -> every unpushed live target is
    `reach_error:<type>`; the cycle's report and exit semantics are those of the publication."""
    _synced(env)
    _reachable(env, how="ok")
    good = _cycle(env)

    def broken(*a, **k):
        raise RuntimeError("cannot start a thread")
    monkeypatch.setattr(TR, "TargetRouter", broken)
    bad = _cycle(env)
    assert bad["reach"]["managed-w1"] == {"ok": False, "via": "preflight", "code": "reach_error:RuntimeError"}
    for k in ("alerts", "lagging", "published", "deferred", "security"):
        assert good[k] == bad[k], k


def test_the_cycle_state_carries_the_same_boot_stamps(env, monkeypatch):
    """C12 / C14: `last_success_mono` and `boot_id` beside the wall-clock fields; the schema stays v1 and the
    parent's reader (only the schema is checked) reads it."""
    _synced(env)
    _reachable(env, how="ok")
    monkeypatch.setattr(TP, "monotonic", lambda: 4242.0)
    monkeypatch.setattr(TP, "current_boot_id", lambda: BOOT)
    _cycle(env)
    st = TP.load_cycle_state(env["reg"].stand)
    assert st["schema"] == "agentnode.trust-cycle-state.v1" and st["last_success_mono"] == 4242.0
    assert st["boot_id"] == BOOT and st["reach"]["managed-w1"]["ok_mono"] == 4242.0 and st["last_success_at"]


# ====================================================================== C11: bounded initialization
def _install_moment(s, now, *, act_age, exit_=0, result="success", status="0"):
    """Right after an install/forward: the cycle timer (re)activated `act_age` s ago, no head cycle state yet."""
    TP.cycle_state_path(s.stand).unlink(missing_ok=True)
    sd = Systemd(now)
    sd.timer(W.CYCLE_TIMER, activated=_us(MONO - act_age))
    sd.service("rmcp-trust-cycle.service", exit_=exit_, result=result, status=status)
    return sd


def test_the_first_minutes_after_an_install_are_initializing(s):
    """C11 / the live finding (3): no cycle yet -> `initializing` (exit 0, no alert) up to 600 s inclusive."""
    now = time.time()
    for age in (0, 300, 600):
        report = _check(s, _install_moment(s, now, act_age=age), now=now)
        assert report["state"] == "initializing" and report["problems"] == [], (age, report["problems"])
        assert {p.split()[0] for p in report["pending"]} == {"trust_cycle_not_succeeding", "target_reach_unknown"}


def test_initialization_expires_at_601_seconds(s):
    """C11 / CC19: one second over the grace with no finished cycle is red."""
    now = time.time()
    report = _check(s, _install_moment(s, now, act_age=601), now=now)
    assert report["state"] == "red" and any("initializing expired after 601 s" in p for p in report["problems"])


@pytest.mark.parametrize("result,status", [("exit-code", "3"), ("exit-code", "5"), ("exit-code", "1"),
                                           ("timeout", "0")])
def test_a_first_cycle_that_failed_is_red_at_once(s, result, status):
    """C11 / CC18: degraded (3), busy (5), failed (1) or killed by its timeout -- red inside the grace."""
    now = time.time()
    sd = _install_moment(s, now, act_age=200, exit_=_us(MONO - 10), result=result, status=status)
    report = _check(s, sd, now=now)
    assert report["state"] == "red" and any("first cycle since activation finished" in p for p in report["problems"])


def test_a_service_exit_from_before_the_activation_does_not_end_initialization(s):
    """negatives: a stale pre-activation exit (the old release's last run) is not this activation's first cycle."""
    now = time.time()
    sd = _install_moment(s, now, act_age=100, exit_=_us(MONO - 400), result="exit-code", status="1")
    assert _check(s, sd, now=now)["state"] == "initializing"


def test_after_the_first_cycle_the_plain_judgement_applies(s):
    """C11: a finished successful first cycle ends initialization; its state makes the report green."""
    now = time.time()
    sd = _install_moment(s, now, act_age=200, exit_=_us(MONO - 10))
    _cycle_state(s, at=now - 10, mono=MONO - 10)
    assert _check(s, sd, now=now)["state"] == "green"


@pytest.mark.parametrize("breakage", ["list", "timer", "ack", "journal"])
def test_unrelated_defects_stay_red_during_initialization(s, breakage):
    """C11 / CC20: only the missing first cycle and reach are pending; broken trust material is red at once."""
    now = time.time()
    sd = _install_moment(s, now, act_age=60)
    if breakage == "list":
        key, cert = s.world._ca()
        Path(s.tls.revocation_list).write_bytes(rl.build(key, cert, {}, number=rl.number_of(s.pair()[0]) + 1,
                                                         now=now - 8 * 86400))
    elif breakage == "timer":
        sd.timer(W.FLOOR_TIMER, file="disabled")
    elif breakage == "ack":
        s.ack("managed-w1", revocation_list_number=99)
    else:
        d = TP.records_dir(s.stand)
        d.mkdir(parents=True, exist_ok=True)
        (d / "r-000000000001.json").write_text("{trunc")
    assert _check(s, sd, now=now)["state"] == "red"


def test_a_reboot_is_governed_by_initialization(s):
    """C12 / negatives: after a boot change the old boot's stamps are not compared; until the first cycle of the
    new boot (Persistent catch-up) the old evidence is pending, not red."""
    now = time.time()
    sd = Systemd(now)
    sd.timer(W.CYCLE_TIMER, activated=_us(30.0))                 # the timer came up with the new boot
    sd.service("rmcp-trust-cycle.service")                       # nothing ran yet in this boot
    _cycle_state(s, at=now - 4 * 3600, mono=MONO + 9999, boot="boot-before")   # the machine was down 4 h
    report = _check(s, sd, now=now, mono=60.0, boot="boot-after")
    assert report["state"] == "initializing", report["problems"]


def test_a_timer_restart_restarts_initialization_and_a_service_restart_does_not(s):
    """negatives: a restarted timer (new activation) is initializing until its first cycle; a cycle run that
    finished after the activation (a service restart, a manual run) ends it."""
    now = time.time()
    sd = _install_moment(s, now, act_age=30)
    assert _check(s, sd, now=now)["state"] == "initializing"
    sd.service("rmcp-trust-cycle.service", exit_=_us(MONO - 5))
    _cycle_state(s, at=now - 5, mono=MONO - 5)
    assert _check(s, sd, now=now)["state"] == "green"


def test_main_exits_zero_while_initializing(monkeypatch):
    for state, rc in (("green", 0), ("initializing", 0), ("red", 1)):
        monkeypatch.setattr(W, "check", lambda stand, **kw: {"state": state, "problems": [], "facts": {}})
        assert W.main(["check", "--stand", "S"]) == rc


# ====================================================================== C13: recovery
def _red_then(s, now, sd_green):
    sd = _healthy(s, now)
    sd.timer(W.FLOOR_TIMER, next_usec=None)                     # no service running: red
    red = _check(s, sd, now=now)
    assert red["state"] == "red"
    return red, _check(s, sd_green, now=now + 3600)


def test_the_first_green_after_red_records_recovery_once(s):
    """C13: `red_since`/`last_red` while red; the first green run records `recovered_at`/`recovered_from` and
    appends ONE recovery line; the next green run appends nothing."""
    now = time.time()
    red, green = _red_then(s, now, _healthy(s, now + 3600))
    assert red["red_since"] == W._iso(now) and red["last_red"]["problems"] and len(red["red_episode"]) == 16
    assert green["state"] == "green" and green["recovered_at"] == W._iso(now + 3600)
    assert green["recovered_from"] == {"red_since": W._iso(now), "episode": red["red_episode"],
                                       "codes": "timer_next_not_finite"}
    assert green["recovery_logged"] is True
    lines = _alerts(s).read_text().splitlines()
    assert lines == ["%s rmcp-trust-watchdog recovered (episode %s, red since %s: timer_next_not_finite)"
                     % (W._iso(now + 3600), red["red_episode"], W._iso(now))]
    again = _check(s, _healthy(s, now + 7200), now=now + 7200)
    assert again["state"] == "green" and _alerts(s).read_text().splitlines() == lines
    assert again["last_red"] == red["last_red"]                  # the last episode stays on record


def test_initializing_is_never_a_recovery(s):
    """C13 / CC23: red, then an install (initializing): the episode goes on; the first GREEN run recovers."""
    now = time.time()
    sd = _healthy(s, now)
    sd.timer(W.FLOOR_TIMER, next_usec=None)
    _check(s, sd, now=now)
    init = _check(s, _install_moment(s, now + 60, act_age=30), now=now + 60)
    assert init["state"] == "initializing" and init["red_since"] == W._iso(now) and "recovered_at" not in init
    assert not _alerts(s).exists()
    green = _check(s, _healthy(s, now + 600), now=now + 600)
    assert green["recovered_from"]["red_since"] == W._iso(now) and len(_alerts(s).read_text().splitlines()) == 1


def test_a_red_report_of_the_parent_release_is_an_episode(s):
    """R4: the parent's red state (no `red_since`) counts as red since its own check."""
    now = time.time()
    s.state.parent.mkdir(parents=True, exist_ok=True)
    s.state.write_text(json.dumps({"state": "red", "checked_at": "2026-10-23T08:20:15Z",
                                   "problems": ["timer_next_not_finite rmcp-trust-cycle.timer (x)"], "facts": {}}))
    green = _check(s, _healthy(s, now), now=now)
    rf = green["recovered_from"]
    assert (rf["red_since"], rf["codes"]) == ("2026-10-23T08:20:15Z", "timer_next_not_finite")
    assert rf["episode"].startswith("p") and len(rf["episode"]) == 16
    assert _alerts(s).read_text().endswith(", red since 2026-10-23T08:20:15Z: timer_next_not_finite)\n")


def test_an_unreadable_previous_state_is_previous_unknown(s):
    """C13 / negatives: no recovery line, the current verdict judged normally (red stays red)."""
    now = time.time()
    s.state.parent.mkdir(parents=True, exist_ok=True)
    s.state.write_text("{trunc")
    green = _check(s, _healthy(s, now), now=now)
    assert green["state"] == "green" and "recovered_at" not in green and not _alerts(s).exists()
    s.state.write_text("{trunc")
    sd = _healthy(s, now)
    sd.timer(W.FLOOR_TIMER, next_usec=None)
    assert _check(s, sd, now=now)["state"] == "red"


def test_an_unwritable_alert_log_is_red_and_recovery_is_owed(s):
    """C13 / CC24: the append fails -> red, not recovered; once the log is writable the next green run appends
    the line exactly once."""
    now = time.time()
    sd = _healthy(s, now)
    sd.timer(W.FLOOR_TIMER, next_usec=None)
    _check(s, sd, now=now)
    _alerts(s).mkdir(parents=True)                               # a directory: no file can be appended
    blocked = _check(s, _healthy(s, now + 3600), now=now + 3600)
    assert blocked["state"] == "red" and "alerts_log_unwritable" in _codes(blocked)
    assert blocked["red_since"] == W._iso(now) and "recovered_at" not in blocked
    _alerts(s).rmdir()
    ok = _check(s, _healthy(s, now + 7200), now=now + 7200)
    assert ok["state"] == "green" and ok["recovered_from"]["red_since"] == W._iso(now)
    assert len(_alerts(s).read_text().splitlines()) == 1


class _Crash(BaseException):
    """A SIGKILL stand-in: nothing after it runs."""


def test_a_crash_around_the_append_never_duplicates_the_line(s, monkeypatch):
    """C13 / CC25: killed after the state said `recovery_logged: false` (before the append) -> the next run
    appends once; killed after the append (before the marker) -> the next run finds the line and adds none."""
    now = time.time()
    sd = _healthy(s, now)
    sd.timer(W.FLOOR_TIMER, next_usec=None)
    _check(s, sd, now=now)
    real_append = W._append_recovery

    def crash_before(log, red_since, line):
        raise _Crash()
    monkeypatch.setattr(W, "_append_recovery", crash_before)
    with pytest.raises(_Crash):
        _check(s, _healthy(s, now + 3600), now=now + 3600)
    assert json.loads(s.state.read_text())["recovery_logged"] is False and not _alerts(s).exists()
    monkeypatch.setattr(W, "_append_recovery", real_append)
    _check(s, _healthy(s, now + 3700), now=now + 3700)            # the dead run's lock is gone: this run owns it
    assert len(_alerts(s).read_text().splitlines()) == 1

    # killed AFTER the line was appended and BEFORE `.done`: the next owner finds the line and adds none
    sd = _healthy(s, now + 3800)
    sd.timer(W.FLOOR_TIMER, next_usec=None)
    _check(s, sd, now=now + 3800)

    def crash_before_done(done):
        raise _Crash()
    real_mark_done = W._mark_done
    monkeypatch.setattr(W, "_mark_done", crash_before_done)
    with pytest.raises(_Crash):
        _check(s, _healthy(s, now + 3900), now=now + 3900)
    assert len(_alerts(s).read_text().splitlines()) == 2
    monkeypatch.setattr(W, "_mark_done", real_mark_done)
    _check(s, _healthy(s, now + 3950), now=now + 3950)
    assert len(_alerts(s).read_text().splitlines()) == 2

    # third episode: the crash comes AFTER the line and `.done`, before the state's marker
    sd = _healthy(s, now + 4000)
    sd.timer(W.FLOOR_TIMER, next_usec=None)
    _check(s, sd, now=now + 4000)
    real_write = W.write_state
    calls = {"n": 0}

    def crash_on_marker(path, doc):
        calls["n"] += 1
        if doc.get("recovery_logged") is True:
            raise _Crash()
        return real_write(path, doc)
    monkeypatch.setattr(W, "write_state", crash_on_marker)
    with pytest.raises(_Crash):
        _check(s, _healthy(s, now + 5000), now=now + 5000)
    monkeypatch.setattr(W, "write_state", real_write)
    assert len(_alerts(s).read_text().splitlines()) == 3
    _check(s, _healthy(s, now + 5100), now=now + 5100)
    lines = _alerts(s).read_text().splitlines()
    assert len(lines) == 3 and len({ln.split("(episode ")[1].split(",")[0] for ln in lines}) == 3
    assert json.loads(s.state.read_text())["recovery_logged"] is True


def test_the_alert_log_is_only_ever_appended_to(s):
    """C13 / CC26: a sentinel history and a line `rmcp-alert@` appended meanwhile stay byte-exact; the watchdog
    adds its line at the end and nothing else."""
    now = time.time()
    _alerts(s).parent.mkdir(parents=True, exist_ok=True)
    sentinel = b"2026-10-22T22:20:15Z rmcp-trust-watchdog.service failed\n\x00binary\xff\n"
    _alerts(s).write_bytes(sentinel)
    sd = _healthy(s, now)
    sd.timer(W.FLOOR_TIMER, next_usec=None)
    _check(s, sd, now=now)
    concurrent = b"2026-10-23T09:20:15Z rmcp-trust-cycle.service failed\n"
    with open(_alerts(s), "ab") as fh:
        fh.write(concurrent)
    _check(s, _healthy(s, now + 3600), now=now + 3600)
    data = _alerts(s).read_bytes()
    assert data.startswith(sentinel + concurrent) and data.count(b"\n") == sentinel.count(b"\n") + 2
    assert data.endswith(b", red since %s: timer_next_not_finite)\n" % W._iso(now).encode())


def test_two_episodes_in_the_same_second_each_get_their_line(s):
    """C13 / F-TWD-R1-002: red -> green -> red -> green within ONE second: two episodes, two ids, two lines."""
    now = float(int(time.time()))
    eps = []
    for _ in range(2):
        sd = _healthy(s, now)
        sd.timer(W.FLOOR_TIMER, next_usec=None)
        eps.append(_check(s, sd, now=now)["red_episode"])
        assert _check(s, _healthy(s, now), now=now)["state"] == "green"
    lines = _alerts(s).read_text().splitlines()
    assert eps[0] != eps[1] and len(lines) == 2
    assert all(("episode %s," % e) in line for e, line in zip(eps, lines))


def _red_episode(s, now):
    sd = _healthy(s, now)
    sd.timer(W.FLOOR_TIMER, next_usec=None)
    return _check(s, sd, now=now)["red_episode"]


def _another_owner(s, ep):
    """Another live run holding the episode's claim (its own open file, its own OS lock)."""
    d = s.state.parent / W.RECOVERY_MARKERS
    d.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(d / (ep + ".claim")), os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    assert W._own(fd)
    return d, fd


@pytest.mark.parametrize("jump", [0.0, 10 * 86400.0, -10 * 86400.0])
def test_a_live_owner_is_never_taken_over_whatever_the_clock_says(s, monkeypatch, jump):
    """C13 / F-TWD-R2-001: while another live run owns the episode (it is slow, or the wall clock jumped forward or
    back), this run appends nothing and stays owed -- no takeover, no clock decides; when the owner finishes, the
    next run sees it logged: exactly one line. The owner's own completion is never invalidated."""
    now = time.time()
    ep = _red_episode(s, now)
    d, fd = _another_owner(s, ep)
    try:
        for k in (1, 2):
            green = _check(s, _healthy(s, now + 60 * k), now=now + 60 * k)
            assert green["state"] == "green" and green["recovery_logged"] is False and not _alerts(s).exists()
        # the recovery step itself under a wall clock thrown forward or back (the stand's trust material would not
        # survive a 10-day jump, so the jump is applied to the step that decides ownership)
        real_time = W.time.time
        monkeypatch.setattr(W.time, "time", lambda: real_time() + jump)
        assert W._recover_once(_alerts(s), ep, "never written") is False and not _alerts(s).exists()
        monkeypatch.setattr(W.time, "time", real_time)
        # the owner resumes and completes exactly as a run does
        W._append_recovery(_alerts(s), ep, "%s rmcp-trust-watchdog recovered (episode %s, red since %s: x)"
                           % (W._iso(now + 200), ep, W._iso(now)))
        W._mark_done(d / (ep + ".done"))
    finally:
        W._disown(fd)
        os.close(fd)
    again = _check(s, _healthy(s, now + 300), now=now + 300)
    assert again["recovery_logged"] is True and len(_alerts(s).read_text().splitlines()) == 1


def test_a_dead_owners_claim_is_owned_by_the_next_run_without_any_clock(s, monkeypatch):
    """C13 / F-TWD-R2-001: the owner died (its OS lock went with it) before writing; the next run owns the claim at
    once -- whatever the clock, even one set years back -- and writes the line once."""
    now = time.time()
    ep = _red_episode(s, now)
    d, fd = _another_owner(s, ep)
    W._disown(fd)
    os.close(fd)                                                 # the process is gone: the kernel released its lock
    real_time = W.time.time
    monkeypatch.setattr(W.time, "time", lambda: 1.0)             # a clock thrown back to 1970 ...
    line = "%s rmcp-trust-watchdog recovered (episode %s, red since %s: x)" % (W._iso(now + 60), ep, W._iso(now))
    assert W._recover_once(_alerts(s), ep, line) is True         # ... decides nothing: the next run owns it at once
    monkeypatch.setattr(W.time, "time", real_time)
    assert len(_alerts(s).read_text().splitlines()) == 1
    green = _check(s, _healthy(s, now + 60), now=now + 60)
    assert green["recovery_logged"] is True and len(_alerts(s).read_text().splitlines()) == 1


def test_the_watchdog_takes_no_lock_for_its_bookkeeping(s, monkeypatch):
    """C13 / D8 negative of attest-renewal: red -> green with a recovery line, no ProcessLock entered at all."""
    from agentnode_sdk.gateway import filelock

    def forbidden(self):
        raise AssertionError("the watchdog took a lock")
    monkeypatch.setattr(filelock.ProcessLock, "__enter__", forbidden)
    now = time.time()
    _red_episode(s, now)
    green = _check(s, _healthy(s, now + 60), now=now + 60)
    assert green["state"] == "green" and green["recovery_logged"] is True
    assert len(_alerts(s).read_text().splitlines()) == 1


# ====================================================================== C14: nothing else changes
def test_the_watchdog_makes_no_connection_and_holds_no_router(s, monkeypatch):
    """C14 / CC29: no socket, no TLS, no router preflight, no worker -- even while judging reach."""
    def forbidden(*a, **k):
        raise AssertionError("the watchdog must not connect")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", forbidden)
    monkeypatch.setattr(TR.TargetRouter, "preflight", forbidden)
    now = time.time()
    assert _check(s, _healthy(s, now), now=now)["state"] == "green"


def test_the_watchdog_changes_nothing_but_its_state_and_its_one_line(s):
    """C14: register, acks, lists, cycle state and journal untouched by a red -> green recovery."""
    import hashlib
    now = time.time()
    sd = _healthy(s, now)
    tree = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for root in (s.stand, s.world.root)
            for p in sorted(Path(root).rglob("*")) if p.is_file()}
    sd.timer(W.FLOOR_TIMER, next_usec=None)
    _check(s, sd, now=now)
    _check(s, _healthy(s, now + 3600), now=now + 3600)
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for root in (s.stand, s.world.root)
             for p in sorted(Path(root).rglob("*")) if p.is_file()}
    cycle = str(TP.cycle_state_path(s.stand))
    assert {k: v for k, v in after.items() if k != cycle} == {k: v for k, v in tree.items() if k != cycle}


def test_the_head_cycle_state_is_readable_by_the_parent_reader(s):
    """C14: the parent's `load_cycle_state` checks only the schema; a head document passes it unchanged."""
    now = time.time()
    _cycle_state(s, at=now, mono=MONO)
    doc = json.loads(TP.cycle_state_path(s.stand).read_text())
    assert doc["schema"] == "agentnode.trust-cycle-state.v1"
    assert TP.load_cycle_state(s.stand) == doc


def test_admission_is_untouched_by_the_watchdog_change():
    """C14 / CC27: `trust_current` still refuses a missing, wrong, expired or content-different ack."""
    now = time.time()
    dep = {"revocation_list_number": 2, "revocation_list_sha256": "a" * 64, "revocation_list_next_update": now + 9e5,
           "tombstones_sha256": "b" * 64, "tombstones_published_at": now - 10, "tombstones_not_after": now + 9e5,
           "content_sha256": "c" * 64}
    prev = dict(dep, revocation_list_number=1, revocation_list_sha256="d" * 64, content_sha256="e" * 64)
    assert T.trust_current(dict(dep), now=now, deployment=dep) is True
    assert T.trust_current(None, now=now, deployment=dep) is False
    assert T.trust_current(dict(dep, revocation_list_number=3), now=now, deployment=dep) is False
    assert T.trust_current(dict(dep), now=now + 1e6, deployment=dep) is False
    assert T.trust_current(dict(prev), now=now, deployment=dep, previous=prev) is False
    assert T.trust_current(dict(prev), now=now, deployment=dep,
                           previous=dict(prev, content_sha256="c" * 64)) is True


def test_the_only_unit_change_is_the_watchdog_timeout():
    """C7 of D7: among the shipped units only rmcp-trust-watchdog.service changed, by one TimeoutStartSec line."""
    text = _shipped(TT.WATCHDOG_SERVICE)
    assert [ln for ln in text.splitlines() if ln.startswith("TimeoutStartSec")] == ["TimeoutStartSec=2min"]
    for name in ("rmcp-trust-cycle.timer", "rmcp-control-floor.timer", "rmcp-trust-watchdog.timer"):
        assert "OnCalendar" in _shipped(name)
    assert "OnCalendar=*-*-* *:20:00 UTC" in _shipped("rmcp-trust-watchdog.timer")
    assert "OnCalendar=*-*-* *:0/5:00 UTC" in _shipped("rmcp-trust-cycle.timer")
