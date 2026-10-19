"""Install, check and remove the trust timers (trust-refresh C1-C5, C16; DEFECT-LIVE-001).

`sdk/deploy/remote-mcp/trust-timers.sh` is a thin wrapper around this module, run as root on the
control host:

  install <release-dir>   validate the release's four units + the cert-check drop-in BEFORE touching
                          /etc/systemd/system, replace each atomically (0644), daemon-reload, enable and
                          RESTART both timers (re-arming them whatever their history), then require both
                          to be loaded, enabled, active and to carry a future, finite next elapse within
                          their bound. Anything else exits non-zero: "active with NEXT=- or infinity" is
                          the defect itself and a hard error, never a success.
  check                   only that last judgement.
  remove                  stop and disable both timers, delete the units, the drop-in, BOTH stamp files
                          and the watchdog's state file, daemon-reload, reset-failed, and prove all of it
                          absent (the rollback to a release without these units).

The unit policy (`unit_problems`) is the same code the tests hold the shipped files to: a timer is
scheduled by the wall clock only (`OnCalendar=` in UTC, `Persistent=true`), never by a monotonic
trigger, and the watchdog has no ordering or dependency on any refresh unit.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from agentnode_sdk.remote_mcp import trust_watchdog as W

REFRESH_SERVICE, REFRESH_TIMER = "rmcp-trust-refresh.service", W.REFRESH_TIMER
WATCHDOG_SERVICE, WATCHDOG_TIMER = "rmcp-trust-watchdog.service", W.WATCHDOG_TIMER
UNITS = (REFRESH_SERVICE, REFRESH_TIMER, WATCHDOG_SERVICE, WATCHDOG_TIMER)
TIMERS = ((REFRESH_TIMER, W.REFRESH_BOUND_SECONDS), (WATCHDOG_TIMER, W.WATCHDOG_BOUND_SECONDS))
DROPIN_DIR, DROPIN = "rmcp-cert-check.service.d", "trust-watchdog.conf"
SOURCE = Path("sdk") / "deploy" / "remote-mcp"
#: A timer scheduled by any of these counts from a boot or an activation -- the defect's mechanism.
MONOTONIC = ("OnActiveSec", "OnBootSec", "OnStartupSec", "OnUnitActiveSec", "OnUnitInactiveSec")
#: The watchdog must not wait for, start with, stop with or need any refresh unit.
DEPENDENCIES = ("Requires", "Requisite", "Wants", "BindsTo", "PartOf", "Upholds", "After", "Before",
                "PropagatesStopTo", "StopPropagatedFrom", "OnSuccess", "OnFailure")
#: What each shipped timer must fire at, in UTC: (hour, minute, second) per day.
SCHEDULE = {REFRESH_TIMER: [(h, 0, 0) for h in (0, 6, 12, 18)],
            WATCHDOG_TIMER: [(h, 20, 0) for h in range(24)]}

Runner = Callable[[list], "tuple[int, str]"]


def _run(argv: list) -> tuple[int, str]:
    env = dict(os.environ, LC_ALL="C", TZ="UTC")
    done = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                          env=env, timeout=120)
    return done.returncode, done.stdout


# ---------------------------------------------------------------------- the unit policy
def parse_unit(text: str) -> dict[str, list[tuple[str, str]]]:
    """Sections -> [(key, value)], in order, duplicates kept (systemd allows repeated keys)."""
    out: dict[str, list[tuple[str, str]]] = {}
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            out.setdefault(section, [])
            continue
        key, sep, value = line.partition("=")
        if sep:
            out.setdefault(section, []).append((key.strip(), value.strip()))
    return out


_FIELD = r"(\*|\d{1,2}(?:/\d{1,2})?)"
_CAL = re.compile(r"^\*-\*-\* %s:%s:%s UTC$" % (_FIELD, _FIELD, _FIELD))


def _expand(field: str, top: int) -> list[int]:
    if field == "*":
        return list(range(top))
    if "/" in field:
        start, step = (int(x) for x in field.split("/"))
        if step <= 0 or start >= top:
            raise ValueError(field)
        return list(range(start, top, step))
    v = int(field)
    if v >= top:
        raise ValueError(field)
    return [v]


def calendar_elapses(spec: str) -> list[tuple[int, int, int]]:
    """Every (hour, minute, second) of one UTC day at which `spec` elapses. Only the restricted form
    `*-*-* H:M:S UTC` (each field `*`, `N` or `N/STEP`) is understood; anything else is a ValueError,
    so an unfamiliar expression is refused instead of guessed."""
    m = _CAL.match(spec.strip())
    if not m:
        raise ValueError("not a daily UTC calendar expression this policy understands: %r" % spec)
    hours, minutes, seconds = _expand(m.group(1), 24), _expand(m.group(2), 60), _expand(m.group(3), 60)
    return sorted((h, mi, s) for h in hours for mi in minutes for s in seconds)


def unit_problems(name: str, text: str) -> list[str]:
    units = parse_unit(text)
    out: list[str] = []
    if name.endswith(".timer"):
        timer = units.get("Timer", [])
        keys = [k for k, _ in timer]
        for k in MONOTONIC:
            if k in keys:
                out.append("%s: monotonic trigger %s" % (name, k))
        cal = [v for k, v in timer if k == "OnCalendar"]
        if not cal:
            out.append("%s: no OnCalendar" % name)
        if ("Persistent", "true") not in timer:
            out.append("%s: Persistent=true missing" % name)
        elapses: list[tuple[int, int, int]] = []
        for spec in cal:
            if not spec.endswith(" UTC"):
                out.append("%s: OnCalendar not pinned to UTC: %r" % (name, spec))
            try:
                elapses += calendar_elapses(spec)
            except ValueError as exc:
                out.append("%s: %s" % (name, exc))
        want = SCHEDULE.get(name)
        if want is not None and cal and sorted(set(elapses)) != want:
            out.append("%s: elapses %s, required %s" % (name, sorted(set(elapses))[:6], want[:6]))
        if name == REFRESH_TIMER and len(set(elapses)) < 4:
            out.append("%s: fewer than 4 UTC elapses a day" % name)
        if not any(k == "WantedBy" and v == "timers.target" for k, v in units.get("Install", [])):
            out.append("%s: not WantedBy=timers.target" % name)
    if name in (WATCHDOG_SERVICE, WATCHDOG_TIMER, DROPIN):
        for section in units.values():
            for k, v in section:
                if k in DEPENDENCIES and "rmcp-trust-refresh" in v:
                    out.append("%s: depends on the refresh (%s=%s)" % (name, k, v))
    if name == WATCHDOG_SERVICE:
        unit = dict(units.get("Unit", []))
        if unit.get("OnFailure") != "rmcp-alert@%n.service":
            out.append("%s: no OnFailure=rmcp-alert@%%n.service" % name)
        if not any(k == "ExecStart" and "trust_watchdog check" in v for k, v in units.get("Service", [])):
            out.append("%s: does not run the watchdog check" % name)
    if name == DROPIN:
        execs = [v for k, v in units.get("Service", []) if k == "ExecStart"]
        if not any("trust_watchdog timer-check" in v and "--unit %s" % WATCHDOG_TIMER in v for v in execs):
            out.append("%s: does not check the watchdog timer" % name)
    return out


# ---------------------------------------------------------------------- install / check / remove
def _etc(root) -> Path:
    return Path(root) / "etc" / "systemd" / "system"


def _stamps(root) -> list[Path]:
    return [Path(root) / "var" / "lib" / "systemd" / "timers" / ("stamp-" + t) for t, _ in TIMERS]


def _state(root) -> Path:
    return Path(root) / W.STATE_FILE.lstrip("/")


def _replace(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name("." + dest.name + ".new")
    data = src.read_bytes()
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(str(tmp), 0o644)
    os.replace(str(tmp), str(dest))


def check(*, run: Runner = _run, now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    out: list[str] = []
    for unit, bound in TIMERS:
        state = W.timer_state(unit, run=lambda argv: run(argv)[1])
        out += W.timer_problems(state, now=now, bound_seconds=bound)
    return out


def install(release_dir, *, root="/", run: Runner = _run, now: Callable[[], float] = time.time) -> dict:
    src = Path(release_dir) / SOURCE
    files = {u: src / u for u in UNITS}
    files[DROPIN] = src / DROPIN_DIR / DROPIN
    problems: list[str] = []
    for name, path in files.items():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            problems.append("%s: unreadable (%s)" % (name, type(exc).__name__))
            continue
        problems += unit_problems(name, text)
    if problems:                                              # nothing under /etc was touched
        return {"installed": False, "stage": "validate", "problems": problems}
    etc = _etc(root)
    for name, path in files.items():
        _replace(path, etc / DROPIN_DIR / DROPIN if name == DROPIN else etc / name)
    steps = [["systemctl", "daemon-reload"],
             ["systemctl", "enable", REFRESH_TIMER, WATCHDOG_TIMER],
             ["systemctl", "restart", REFRESH_TIMER, WATCHDOG_TIMER]]
    for argv in steps:
        rc, said = run(argv)
        if rc != 0:
            return {"installed": False, "stage": " ".join(argv[1:2]), "problems": [said.strip()[-300:]]}
    problems = check(run=run, now=now())
    return {"installed": not problems, "stage": "verify", "problems": problems}


def remove(*, root="/", run: Runner = _run) -> dict:
    timers = [t for t, _ in TIMERS]
    run(["systemctl", "disable", "--now"] + timers)
    etc = _etc(root)
    for name in UNITS:
        (etc / name).unlink(missing_ok=True)
    (etc / DROPIN_DIR / DROPIN).unlink(missing_ok=True)
    try:
        (etc / DROPIN_DIR).rmdir()
    except OSError:
        pass                                                  # not empty or absent: someone else's drop-ins stay
    for stamp in _stamps(root):
        stamp.unlink(missing_ok=True)
    _state(root).unlink(missing_ok=True)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "reset-failed"] + list(UNITS))
    left = [str(p) for p in [etc / n for n in UNITS] + [etc / DROPIN_DIR / DROPIN] + _stamps(root) + [_state(root)]
            if p.exists()]
    left += [str(p) for p in (etc / "timers.target.wants").glob("rmcp-trust-*")] if (etc / "timers.target.wants").exists() else []
    for t in timers:
        _rc, said = run(["systemctl", "show", t, "-p", "LoadState,ActiveState"])
        if "LoadState=not-found" not in said or "ActiveState=active" in said:
            left.append("%s still known to systemd (%s)" % (t, " ".join(said.split())))
    return {"removed": not left, "left": left}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="trust-timers")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install").add_argument("release_dir")
    sub.add_parser("check")
    sub.add_parser("remove")
    args = p.parse_args(argv)
    if args.cmd == "install":
        done = install(args.release_dir)
        for line in done["problems"]:
            print("ALERT %s" % line)
        print("TRUST_TIMERS_%s stage=%s" % ("INSTALLED" if done["installed"] else "INSTALL_FAILED", done["stage"]))
        return 0 if done["installed"] else 1
    if args.cmd == "check":
        problems = check()
        for line in problems:
            print("ALERT %s" % line)
        print("TRUST_TIMERS_%s" % ("RED" if problems else "OK"))
        return 1 if problems else 0
    done = remove()
    for line in done["left"]:
        print("LEFT %s" % line)
    print("TRUST_TIMERS_%s" % ("REMOVED" if done["removed"] else "REMOVE_INCOMPLETE"))
    return 0 if done["removed"] else 1


__all__ = ["DROPIN", "DROPIN_DIR", "SCHEDULE", "TIMERS", "UNITS", "calendar_elapses", "check", "install", "main",
           "parse_unit", "remove", "unit_problems"]


if __name__ == "__main__":
    sys.exit(main())
