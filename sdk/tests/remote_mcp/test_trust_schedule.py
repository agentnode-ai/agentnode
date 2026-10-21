"""The trust timers are scheduled by the wall clock and always have a finite next run
(trust-refresh C1-C5, C16; DEFECT-LIVE-001).

The shipped unit files are held to the policy in `trust_timers.unit_problems`, the calendar is
evaluated by a pure-Python evaluator everywhere and by `systemd-analyze` where it exists, and the
installer/remover run against a temporary root with a recording stand-in for `systemctl` that
reproduces what systemd 257 printed for the live defect (`next: null`, monotonic `infinity`).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from agentnode_sdk.remote_mcp import trust_timers as TT
from agentnode_sdk.remote_mcp import trust_watchdog as W

SDK = Path(__file__).resolve().parents[2]
DEPLOY = SDK / "deploy" / "remote-mcp"
SHIPPED = {name: (DEPLOY / name).read_text(encoding="utf-8") for name in TT.UNITS}
SHIPPED[TT.DROPIN] = (DEPLOY / TT.DROPIN_DIR / TT.DROPIN).read_text(encoding="utf-8")
OLD_TIMER = ("[Unit]\nDescription=AgentNode remote MCP: trust distribution, four times a day\n\n[Timer]\n"
             "OnBootSec=5min\nOnUnitActiveSec=6h\nRandomizedDelaySec=5min\nPersistent=true\n\n"
             "[Install]\nWantedBy=timers.target\n")


def _timer(text, name=TT.REFRESH_TIMER):
    return TT.unit_problems(name, text)


# ---------------------------------------------------------------------- C1 / C2 / C4: the shipped units
def test_shipped_units_satisfy_the_policy():
    for name, text in SHIPPED.items():
        assert TT.unit_problems(name, text) == [], name


def test_refresh_timer_literal_schedule():
    timer = dict(TT.parse_unit(SHIPPED[TT.REFRESH_TIMER])["Timer"])
    assert timer == {"OnCalendar": "*-*-* 00/6:00:00 UTC", "Persistent": "true",
                     "RandomizedDelaySec": "5min", "AccuracySec": "1min"}


def test_watchdog_timer_literal_schedule():
    timer = dict(TT.parse_unit(SHIPPED[TT.WATCHDOG_TIMER])["Timer"])
    assert timer == {"OnCalendar": "*-*-* *:20:00 UTC", "Persistent": "true", "AccuracySec": "1min"}


def test_the_old_timer_is_refused():
    found = _timer(OLD_TIMER)
    assert any("monotonic trigger OnUnitActiveSec" in p for p in found)
    assert any("monotonic trigger OnBootSec" in p for p in found)
    assert any("no OnCalendar" in p for p in found)


@pytest.mark.parametrize("line", ["OnUnitActiveSec=6h", "OnBootSec=5min", "OnUnitInactiveSec=6h",
                                  "OnStartupSec=1min", "OnActiveSec=1h"])
def test_any_monotonic_trigger_is_refused(line):
    text = SHIPPED[TT.REFRESH_TIMER].replace("Persistent=true", "Persistent=true\n" + line)
    assert any("monotonic trigger " + line.split("=")[0] in p for p in _timer(text))


def test_calendar_replaced_by_activation_interval_is_refused():
    text = SHIPPED[TT.REFRESH_TIMER].replace("OnCalendar=*-*-* 00/6:00:00 UTC", "OnUnitActiveSec=6h")
    found = _timer(text)
    assert any("no OnCalendar" in p for p in found) and any("OnUnitActiveSec" in p for p in found)


def test_persistent_is_required():
    found = _timer(SHIPPED[TT.REFRESH_TIMER].replace("Persistent=true\n", ""))
    assert any("Persistent=true missing" in p for p in found)
    found = _timer(SHIPPED[TT.WATCHDOG_TIMER].replace("Persistent=true\n", ""), TT.WATCHDOG_TIMER)
    assert any("Persistent=true missing" in p for p in found)


def test_three_elapses_a_day_are_refused():
    found = _timer(SHIPPED[TT.REFRESH_TIMER].replace("00/6:00:00", "00/8:00:00"))
    assert any("fewer than 4 UTC elapses" in p for p in found)


def test_the_utc_pin_is_required():
    found = _timer(SHIPPED[TT.REFRESH_TIMER].replace(":00:00 UTC", ":00:00"))
    assert any("not pinned to UTC" in p for p in found)


@pytest.mark.parametrize("dep", ["Requires=rmcp-trust-refresh.service", "After=rmcp-trust-refresh.timer",
                                 "Wants=rmcp-trust-refresh.service", "BindsTo=rmcp-trust-refresh.service",
                                 "PartOf=rmcp-trust-refresh.timer", "Requisite=rmcp-trust-refresh.service"])
def test_the_watchdog_never_depends_on_the_refresh(dep):
    text = SHIPPED[TT.WATCHDOG_SERVICE].replace("After=network-online.target", "After=network-online.target\n" + dep)
    assert any("depends on what it watches" in p for p in TT.unit_problems(TT.WATCHDOG_SERVICE, text))
    ttext = "[Unit]\n" + dep + "\n" + SHIPPED[TT.WATCHDOG_TIMER]
    assert any("depends on what it watches" in p for p in TT.unit_problems(TT.WATCHDOG_TIMER, ttext))


def test_the_watchdog_alerts_on_failure_and_runs_the_check():
    text = SHIPPED[TT.WATCHDOG_SERVICE]
    assert any("OnFailure" in p for p in TT.unit_problems(TT.WATCHDOG_SERVICE, text.replace("OnFailure=", "X-Off=")))
    assert any("does not run" in p for p in TT.unit_problems(TT.WATCHDOG_SERVICE, text.replace("trust_watchdog check", "x")))


def test_the_cert_check_watches_the_watchdog_timer():
    """C5: the independent daily certificate check (drop-in) checks the WATCHDOG's timer."""
    execs = [v for k, v in TT.parse_unit(SHIPPED[TT.DROPIN])["Service"] if k == "ExecStart"]
    assert any("trust_watchdog timer-check" in v and "--unit rmcp-trust-watchdog.timer" in v for v in execs)
    assert "--bound-seconds %d" % W.WATCHDOG_BOUND_SECONDS in " ".join(execs)
    broken = SHIPPED[TT.DROPIN].replace("--unit rmcp-trust-watchdog.timer", "--unit rmcp-cert-renew.timer")
    assert any("does not check the watchdog timer" in p for p in TT.unit_problems(TT.DROPIN, broken))
    # and the refresh never watches the watchdog (no mutual dependency)
    assert "rmcp-trust-watchdog" not in SHIPPED[TT.REFRESH_SERVICE] + SHIPPED[TT.REFRESH_TIMER]


# ---------------------------------------------------------------------- C2: the calendar
def test_pure_evaluator_four_utc_elapses_six_hours_apart():
    spec = dict(TT.parse_unit(SHIPPED[TT.REFRESH_TIMER])["Timer"])["OnCalendar"]
    el = TT.calendar_elapses(spec)
    assert el == [(0, 0, 0), (6, 0, 0), (12, 0, 0), (18, 0, 0)]
    secs = [h * 3600 + m * 60 + s for h, m, s in el] + [86400]
    assert max(b - a for a, b in zip(secs, secs[1:])) <= 6 * 3600


@pytest.mark.parametrize("spec", ["daily", "*-*-* 00/6:00:00", "Mon *-*-* 00:00:00 UTC", "*-*-* 25:00:00 UTC",
                                  "*-*-* 00/0:00:00 UTC", "*-*-* 00,12:00:00 UTC"])
def test_pure_evaluator_refuses_what_it_does_not_understand(spec):
    with pytest.raises(ValueError):
        TT.calendar_elapses(spec)


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="no systemd-analyze here; the pure evaluator above always runs")
def test_systemd_analyze_agrees():
    for name, want in TT.SCHEDULE.items():
        spec = dict(TT.parse_unit(SHIPPED[name])["Timer"])["OnCalendar"]
        out = subprocess.run(["systemd-analyze", "calendar", "--iterations=8", spec], stdout=subprocess.PIPE,
                             universal_newlines=True, env={"TZ": "UTC", "LC_ALL": "C"}, check=True).stdout
        times = [line.split()[-2] for line in out.splitlines() if "Iteration" in line or "Next elapse" in line]
        hms = sorted({tuple(int(x) for x in t.split(":")) for t in times})
        assert set(hms) <= set(want) and len(hms) == min(8, len(want)), (name, out)


# ---------------------------------------------------------------------- C3: timer state judgement
NOW = 1_800_000_000.0


def _state(next_usec, **kw):
    s = {"unit": TT.REFRESH_TIMER, "LoadState": "loaded", "ActiveState": "active", "UnitFileState": "enabled",
         "NextElapseUSecMonotonic": "0", "next_usec": next_usec}
    s.update(kw)
    return s


def test_a_finite_future_elapse_within_the_bound_is_green():
    assert W.timer_problems(_state(int((NOW + 3600) * 1e6)), now=NOW, bound_seconds=W.REFRESH_BOUND_SECONDS) == []


@pytest.mark.parametrize("value", [None, 0, -5, "", "infinity", "n/a", True, "unparsable", 1.5e15])
def test_no_finite_next_is_red(value):
    found = W.timer_problems(_state(value), now=NOW, bound_seconds=W.REFRESH_BOUND_SECONDS)
    assert any(p.startswith("timer_next_not_finite") for p in found), found


def test_a_past_next_is_red():
    found = W.timer_problems(_state(int((NOW - 1) * 1e6)), now=NOW, bound_seconds=W.REFRESH_BOUND_SECONDS)
    assert any(p.startswith("timer_next_in_past") for p in found)


def test_a_next_beyond_the_bound_is_red():
    found = W.timer_problems(_state(int((NOW + W.REFRESH_BOUND_SECONDS + 1) * 1e6)), now=NOW,
                             bound_seconds=W.REFRESH_BOUND_SECONDS)
    assert any(p.startswith("timer_next_too_far") for p in found)


@pytest.mark.parametrize("field,value,code", [("LoadState", "not-found", "timer_not_loaded"),
                                              ("UnitFileState", "disabled", "timer_not_enabled"),
                                              ("UnitFileState", "", "timer_not_enabled"),
                                              ("ActiveState", "inactive", "timer_not_active"),
                                              ("ActiveState", "failed", "timer_not_active")])
def test_timer_required_states(field, value, code):
    found = W.timer_problems(_state(int((NOW + 60) * 1e6), **{field: value}), now=NOW,
                             bound_seconds=W.REFRESH_BOUND_SECONDS)
    assert any(p.startswith(code) for p in found)


# What systemd 257 printed on the control host (2026-10-19) for a healthy calendar timer, for the
# reinstalled monotonic timer of DEFECT-LIVE-001, and for a unit that does not exist.
SHOW_OK = ("NextElapseUSecMonotonic=0\nLastTriggerUSec=Mon 2026-10-19 00:17:16 UTC\nLoadState=loaded\n"
           "ActiveState=active\nUnitFileState=enabled\n")
SHOW_DEFECT = ("NextElapseUSecMonotonic=infinity\nLastTriggerUSec=Mon 2026-10-19 08:27:22 UTC\nLoadState=loaded\n"
               "ActiveState=active\nUnitFileState=enabled\n")
SHOW_MISSING = "NextElapseUSecMonotonic=infinity\nLastTriggerUSec=\nLoadState=not-found\nActiveState=inactive\nUnitFileState=\n"


def _runner(show, listed):
    def run(argv):
        if argv[1] == "show":
            return show
        if argv[1] == "list-timers":
            return listed
        raise AssertionError(argv)
    return run


def test_parse_the_real_healthy_output():
    listed = json.dumps([{"next": int((NOW + 3600) * 1e6), "left": 1, "last": 1, "passed": 1,
                          "unit": TT.REFRESH_TIMER, "activates": "rmcp-trust-refresh.service"}])
    st = W.timer_state(TT.REFRESH_TIMER, run=_runner(SHOW_OK, listed))
    assert W.next_elapse(st) == NOW + 3600
    assert W.timer_problems(st, now=NOW, bound_seconds=W.REFRESH_BOUND_SECONDS) == []


def test_parse_the_real_defect_output():
    listed = json.dumps([{"next": None, "left": None, "last": 1792399113020379, "passed": 0,
                          "unit": TT.REFRESH_TIMER, "activates": "rmcp-trust-refresh.service"}])
    st = W.timer_state(TT.REFRESH_TIMER, run=_runner(SHOW_DEFECT, listed))
    found = W.timer_problems(st, now=NOW, bound_seconds=W.REFRESH_BOUND_SECONDS)
    assert st["ActiveState"] == "active" and [p.split()[0] for p in found] == ["timer_next_not_finite"]
    assert "infinity" in found[0]


def test_parse_a_missing_unit():
    st = W.timer_state(TT.REFRESH_TIMER, run=_runner(SHOW_MISSING, "[]"))
    codes = {p.split()[0] for p in W.timer_problems(st, now=NOW, bound_seconds=W.REFRESH_BOUND_SECONDS)}
    assert codes == {"timer_not_loaded", "timer_not_enabled", "timer_not_active", "timer_next_not_finite"}


def test_unparsable_json_is_not_finite():
    st = W.timer_state(TT.REFRESH_TIMER, run=_runner(SHOW_OK, "{not json"))
    assert W.next_elapse(st) is None


# ---------------------------------------------------------------------- install / check / remove
class FakeSystemd:
    """Records every call; keeps per-timer state; answers show/list-timers like systemd 257.
    `mode` decides the next elapse a (re)started timer gets: "calendar" (finite, +1 h), "defect"
    (null -- what the reinstalled monotonic timer did) or "far" (beyond the bound)."""

    def __init__(self, root: Path, mode="calendar", now=None):
        self.root, self.mode, self.calls = root, mode, []
        self.now = now or time.time()
        self.timers: dict[str, dict] = {}

    def _etc(self):
        return self.root / "etc" / "systemd" / "system"

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[0] == "env":                                  # targets_cli attest-bootstrap / trust-import-legacy
            return 0, "bootstrapped" if argv[-1] == "attest-bootstrap" else "nothing to import"
        verb = argv[1]
        if verb in ("daemon-reload", "reset-failed"):
            return 0, ""
        if verb == "enable":
            for u in [a for a in argv[2:] if not a.startswith("--")]:
                wants = self._etc() / "timers.target.wants"
                wants.mkdir(parents=True, exist_ok=True)
                (wants / u).write_text("link")
                self.timers.setdefault(u, {})["enabled"] = True
            return 0, ""
        if verb == "restart":
            for u in argv[2:]:
                t = self.timers.setdefault(u, {})
                t["active"] = True
                soon = 60 if u in (TT.CYCLE_TIMER, TT.FLOOR_TIMER) else 3600      # the 5-minute timers
                t["next"] = {"calendar": int((self.now + soon) * 1e6), "defect": None,
                             "far": int((self.now + 30 * 3600) * 1e6)}[self.mode]
            return 0, ""
        if verb == "disable":
            for u in [a for a in argv[2:] if not a.startswith("--")]:
                (self._etc() / "timers.target.wants" / u).unlink(missing_ok=True)
                self.timers[u] = {"enabled": False, "active": False, "next": None}
            return 0, ""
        if verb == "show":
            u = argv[2]
            t = self.timers.get(u, {})
            loaded = (self._etc() / u).exists()
            return 0, ("LoadState=%s\nActiveState=%s\nUnitFileState=%s\nNextElapseUSecMonotonic=%s\n"
                       % ("loaded" if loaded else "not-found", "active" if t.get("active") and loaded else "inactive",
                          "enabled" if t.get("enabled") and loaded else "", "0" if t.get("next") else "infinity"))
        if verb == "list-timers":
            u = argv[-1]
            t = self.timers.get(u)
            if not t or not (self._etc() / u).exists():
                return 0, "[]"
            return 0, json.dumps([{"next": t.get("next"), "unit": u}])
        raise AssertionError(argv)


@pytest.fixture
def release(tmp_path):
    rel = tmp_path / "release"
    shutil.copytree(DEPLOY, rel / "sdk" / "deploy" / "remote-mcp")
    return rel


def _installed(root):
    etc = root / "etc" / "systemd" / "system"
    return sorted(p.relative_to(etc).as_posix() for p in etc.rglob("*") if p.is_file()) if etc.exists() else []


def test_install_then_check_green(tmp_path, release):
    root = tmp_path / "root"
    fake = FakeSystemd(root)
    done = TT.install(release, root=root, run=fake, now=lambda: fake.now)
    assert done == {"installed": True, "stage": "verify", "problems": []}
    assert set(_installed(root)) >= {TT.DROPIN_DIR + "/" + TT.DROPIN, *TT.UNITS}
    for name in TT.UNITS:                                     # byte-exact from the release, 0644
        assert (root / "etc/systemd/system" / name).read_bytes() == (release / TT.SOURCE / name).read_bytes()
    assert fake.calls[0][0] == "env" and fake.calls[0][-1] == "attest-bootstrap"   # state before any timer
    assert fake.calls[1][0] == "env" and fake.calls[1][-1] == "trust-import-legacy"  # predecessor layout (R3)
    verbs = [c[1] for c in fake.calls[2:]]
    assert verbs[:3] == ["daemon-reload", "enable", "restart"]
    assert ["systemctl", "restart", TT.REFRESH_TIMER, TT.WATCHDOG_TIMER, TT.RENEW_TIMER, TT.CYCLE_TIMER,
            TT.FLOOR_TIMER] in fake.calls


def test_install_hard_fails_on_an_active_timer_without_next(tmp_path, release):
    """C3 / goal point 2: active with NEXT=- or infinity after install is an install FAILURE."""
    root = tmp_path / "root"
    fake = FakeSystemd(root, mode="defect")
    done = TT.install(release, root=root, run=fake, now=lambda: fake.now)
    assert done["installed"] is False and done["stage"] == "verify"
    assert sum(p.startswith("timer_next_not_finite") for p in done["problems"]) == 5


def test_install_hard_fails_on_a_next_beyond_the_bound(tmp_path, release):
    root = tmp_path / "root"
    fake = FakeSystemd(root, mode="far")
    done = TT.install(release, root=root, run=fake, now=lambda: fake.now)
    assert done["installed"] is False and any(p.startswith("timer_next_too_far") for p in done["problems"])


def test_install_validates_before_touching_etc(tmp_path, release):
    (release / TT.SOURCE / TT.REFRESH_TIMER).write_text(OLD_TIMER)
    root = tmp_path / "root"
    fake = FakeSystemd(root)
    done = TT.install(release, root=root, run=fake)
    assert done["installed"] is False and done["stage"] == "validate"
    assert _installed(root) == [] and fake.calls == []


def test_install_refuses_a_missing_unit(tmp_path, release):
    (release / TT.SOURCE / TT.WATCHDOG_SERVICE).unlink()
    root = tmp_path / "root"
    done = TT.install(release, root=root, run=FakeSystemd(root))
    assert done["installed"] is False and any("unreadable" in p for p in done["problems"])
    assert _installed(root) == []


def test_reinstall_over_a_stamp_and_old_units_is_finite(tmp_path, release):
    """The defect's path: an earlier install (stamps, units) then a reinstall -- finite again."""
    root = tmp_path / "root"
    fake = FakeSystemd(root)
    assert TT.install(release, root=root, run=fake, now=lambda: fake.now)["installed"]
    for stamp in TT._stamps(root):
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text("")
    for _ in range(3):
        assert TT.install(release, root=root, run=fake, now=lambda: fake.now)["installed"]
        assert TT.check(run=fake, now=fake.now) == []


def test_remove_leaves_no_residue(tmp_path, release):
    """C16 / CC36: units, drop-in, enablement links, BOTH stamps and the watchdog state all go."""
    root = tmp_path / "root"
    fake = FakeSystemd(root)
    assert TT.install(release, root=root, run=fake, now=lambda: fake.now)["installed"]
    for stamp in TT._stamps(root):
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text("")
    TT._state(root).parent.mkdir(parents=True, exist_ok=True)
    TT._state(root).write_text("{}")
    other = root / "etc/systemd/system" / TT.DROPIN_DIR / "someone-else.conf"
    other.write_text("[Service]\n")
    done = TT.remove(root=root, run=fake, preflight=False)
    assert done == {"removed": True, "left": []}
    assert _installed(root) == [TT.DROPIN_DIR + "/someone-else.conf"]   # only what is not ours stays
    assert not any(s.exists() for s in TT._stamps(root)) and not TT._state(root).exists()
    assert ["systemctl", "disable", "--now", TT.REFRESH_TIMER, TT.WATCHDOG_TIMER, TT.RENEW_TIMER,
            TT.CYCLE_TIMER] in fake.calls                     # the floor timer is restored, never just stopped


@pytest.mark.parametrize("left_behind", ["stamp-refresh", "stamp-watchdog", "state", "wants-link"])
def test_remove_reports_what_it_could_not_remove(tmp_path, release, monkeypatch, left_behind):
    root = tmp_path / "root"
    fake = FakeSystemd(root)
    assert TT.install(release, root=root, run=fake, now=lambda: fake.now)["installed"]
    keep = {"stamp-refresh": TT._stamps(root)[0], "stamp-watchdog": TT._stamps(root)[1], "state": TT._state(root),
            "wants-link": root / "etc/systemd/system/timers.target.wants" / TT.REFRESH_TIMER}[left_behind]
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text("x")
    real_unlink = Path.unlink

    def stubborn(self, missing_ok=False):
        if self == keep:
            return None
        return real_unlink(self, missing_ok=missing_ok)
    monkeypatch.setattr(Path, "unlink", stubborn)
    real_call = fake.__call__

    def no_disable(argv):
        if argv[1] == "disable" and left_behind == "wants-link":
            fake.calls.append(list(argv))
            return 0, ""
        return real_call(argv)
    done = TT.remove(root=root, run=no_disable, preflight=False)
    assert done["removed"] is False and any(str(keep) in item or keep.name in item for item in done["left"])


def test_cli_exit_codes(release, monkeypatch, capsys):
    monkeypatch.setattr(TT, "install", lambda rel, **kw: {"installed": False, "stage": "verify",
                                                     "problems": ["timer_next_not_finite x"]})
    assert TT.main(["install", str(release)]) == 1
    assert "TRUST_TIMERS_INSTALL_FAILED" in capsys.readouterr().out
    monkeypatch.setattr(TT, "remove", lambda **kw: {"removed": False, "left": ["stamp"]})
    assert TT.main(["remove"]) == 1
    monkeypatch.setattr(TT, "check", lambda: ["timer_next_not_finite rmcp-trust-refresh.timer"])
    assert TT.main(["check"]) == 1
    monkeypatch.setattr(TT, "check", lambda: [])
    assert TT.main(["check"]) == 0
