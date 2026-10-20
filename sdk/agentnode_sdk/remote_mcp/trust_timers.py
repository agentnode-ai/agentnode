"""Install, check and remove the trust and attestation-renewal timers (trust-refresh C1-C5, C16;
DEFECT-LIVE-001; attest-renewal D5, D8, D10).

`sdk/deploy/remote-mcp/trust-timers.sh` is a thin wrapper around this module, run as root on the
control host:

  install <release-dir>   validate the release's six units + the cert-check drop-in BEFORE touching
                          /etc/systemd/system, initialise the renewal state from the verified register,
                          replace each unit atomically (0644), daemon-reload, enable and RESTART all three
                          timers (re-arming them whatever their history), then require each to be loaded,
                          enabled, active and to carry a future, finite next elapse within its bound.
                          Anything else exits non-zero: "active with NEXT=- or infinity" is the defect
                          itself and a hard error, never a success.
  check                   only that last judgement.
  preflight               the rollback's quiescence check alone (see `quiesce`).
  remove                  ONLY when quiescent (no renewal running, no probe/renewal lock held, no open
                          head-only operation, >= 48 h left on the managed default): stop and disable the
                          timers, delete the units, the drop-in, the stamp files, the watchdog's and the
                          renewal's state files, daemon-reload, reset-failed, and prove all of it absent
                          (the rollback to a release without these units). Lock files stay (inert); the
                          register and every other monotonic record are never touched.

The unit policy (`unit_problems`) is the same code the tests hold the shipped files to: a timer is
scheduled by the wall clock only (`OnCalendar=` in UTC, `Persistent=true`), never by a monotonic
trigger, and the watchdog has no ordering or dependency on any refresh unit.
"""
from __future__ import annotations

import argparse
import json
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
RENEW_SERVICE, RENEW_TIMER = "rmcp-attest-renew.service", W.RENEW_TIMER
UNITS = (REFRESH_SERVICE, REFRESH_TIMER, WATCHDOG_SERVICE, WATCHDOG_TIMER, RENEW_SERVICE, RENEW_TIMER)
TIMERS = ((REFRESH_TIMER, W.REFRESH_BOUND_SECONDS), (WATCHDOG_TIMER, W.WATCHDOG_BOUND_SECONDS),
          (RENEW_TIMER, W.RENEW_BOUND_SECONDS))
STAND = "/var/lib/agentnode-mcp/stand"
#: The managed default must keep at least this much validity across a rollback: the a36ddcdd release
#: cannot renew it (its `probe` refuses to take the default out of `active`).
ROLLBACK_MIN_VALIDITY_SECONDS = 48 * 3600
#: How long a rollback waits for a running renewal service to finish after its timer was stopped.
QUIESCE_WAIT_SECONDS = 900
DROPIN_DIR, DROPIN = "rmcp-cert-check.service.d", "trust-watchdog.conf"
SOURCE = Path("sdk") / "deploy" / "remote-mcp"
#: A timer scheduled by any of these counts from a boot or an activation -- the defect's mechanism.
MONOTONIC = ("OnActiveSec", "OnBootSec", "OnStartupSec", "OnUnitActiveSec", "OnUnitInactiveSec")
#: The watchdog must not wait for, start with, stop with or need any refresh unit.
DEPENDENCIES = ("Requires", "Requisite", "Wants", "BindsTo", "PartOf", "Upholds", "After", "Before",
                "PropagatesStopTo", "StopPropagatedFrom", "OnSuccess", "OnFailure")
#: What each shipped timer must fire at, in UTC: (hour, minute, second) per day.
SCHEDULE = {REFRESH_TIMER: [(h, 0, 0) for h in (0, 6, 12, 18)],
            WATCHDOG_TIMER: [(h, 20, 0) for h in range(24)],
            RENEW_TIMER: [(h, 30, 0) for h in (3, 9, 15, 21)]}

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
        if name in (REFRESH_TIMER, RENEW_TIMER) and len(set(elapses)) < 4:
            out.append("%s: fewer than 4 UTC elapses a day" % name)
        if not any(k == "WantedBy" and v == "timers.target" for k, v in units.get("Install", [])):
            out.append("%s: not WantedBy=timers.target" % name)
    if name in (WATCHDOG_SERVICE, WATCHDOG_TIMER, DROPIN):
        for section in units.values():
            for k, v in section:
                if k in DEPENDENCIES and ("rmcp-trust-refresh" in v or "rmcp-attest-renew" in v):
                    out.append("%s: depends on what it watches (%s=%s)" % (name, k, v))
    if name == RENEW_SERVICE:
        unit, service = dict(units.get("Unit", [])), units.get("Service", [])
        if unit.get("OnFailure") != "rmcp-alert@%n.service":
            out.append("%s: no OnFailure=rmcp-alert@%%n.service" % name)
        if not any(k == "ExecStart" and "targets_cli" in v and "attest-renew" in v for k, v in service):
            out.append("%s: does not run targets_cli attest-renew" % name)
        if not any(k == "TimeoutStartSec" for k, _ in service):
            out.append("%s: no TimeoutStartSec (a hung renewal must end)" % name)
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


def _renew_state(root, stand) -> Path:
    """The renewal state of `stand` (`<stand>/../alerts/attest-renew.json`); for the production stand
    under `root` (a test's stand-in for /)."""
    from agentnode_sdk.remote_mcp.target_ops import renew_state_path
    if stand != STAND:
        return renew_state_path(stand)
    return Path(root) / "var" / "lib" / "agentnode-mcp" / "alerts" / "attest-renew.json"


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


def install(release_dir, *, root="/", run: Runner = _run, now: Callable[[], float] = time.time,
            stand: str = STAND) -> dict:
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
    timers = [t for t, _ in TIMERS]
    # the renewal state starts from the verified register BEFORE any timer runs (D8: no false "missing")
    rc, said = run(["env", "PYTHONPATH=%s" % (Path(release_dir) / "sdk"), sys.executable, "-m",
                    "agentnode_sdk.remote_mcp.targets_cli", "--stand", stand, "attest-bootstrap"])
    if rc != 0:
        return {"installed": False, "stage": "bootstrap", "problems": [said.strip()[-300:]]}
    for name, path in files.items():
        _replace(path, etc / DROPIN_DIR / DROPIN if name == DROPIN else etc / name)
    steps = [["systemctl", "daemon-reload"],
             ["systemctl", "enable"] + timers,
             ["systemctl", "restart"] + timers]
    for argv in steps:
        rc, said = run(argv)
        if rc != 0:
            return {"installed": False, "stage": " ".join(argv[1:2]), "problems": [said.strip()[-300:]]}
    problems = check(run=run, now=now())
    return {"installed": not problems, "stage": "verify", "problems": problems}


def quiesce(*, stand: str = STAND, run: Runner = _run, now: Callable[[], float] = time.time,
            sleep: Callable[[float], None] = time.sleep, wait_seconds: float = QUIESCE_WAIT_SECONDS) -> list[str]:
    """Rollback preflight (attest-renewal D10): stop scheduling renewals, wait for a running renewal to end,
    then refuse while any probe/renewal lock is held, any head-only operation is open, or the managed
    default has less than 48 h of validity left (the older release cannot renew it). [] = quiescent."""
    from agentnode_sdk.remote_mcp import target_ops as O
    from agentnode_sdk.remote_mcp import targets as T

    problems: list[str] = []
    run(["systemctl", "disable", "--now", RENEW_TIMER])
    deadline = now() + wait_seconds
    while True:
        _rc, said = run(["systemctl", "show", RENEW_SERVICE, "-p", "ActiveState"])
        if "ActiveState=activating" not in said and "ActiveState=active" not in said:
            break
        if now() >= deadline:
            problems.append("renewal service still running after %d s" % int(wait_seconds))
            break
        sleep(5)
    tdir = Path(stand) / "targets"
    held = W.held_flock_inodes()
    locks = sorted(tdir.glob(O.PROBE_LOCK_PREFIX + "*.lock")) + [Path(str(tdir / O.RENEW_LOCK_NAME) + ".lock")]
    for lock in locks:
        state = W._lock_held(lock, held)
        if state in ("lock held", "lock state unknown"):
            problems.append("%s: %s" % (lock.name, state))
    for f in sorted((tdir / "ops").glob("op-*.json")) if (tdir / "ops").is_dir() else []:
        try:
            op = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            problems.append("operation unreadable: %s" % f.name)
            continue
        if op.get("state") == "open" and op.get("kind") in O.HEAD_ONLY_KINDS + ("probe",):
            problems.append("open %s operation %s (%s): close it with `targets_cli resume-ops` first"
                            % (op.get("kind"), op.get("op_id"), op.get("target_id")))
    try:
        op_pub = bytes.fromhex(Path(stand, "operator_pub.hex").read_text(encoding="utf-8").strip())
        snap = T.Register(stand, op_pub, pin_clock=False).load()
        d = snap.target(snap.default_target)
        left = float((d.attestation or {}).get("expires_at", 0)) - snap.now if d else 0.0
        if left < ROLLBACK_MIN_VALIDITY_SECONDS:
            problems.append("managed default %s has %.1f h of validity left; below 48 h the older release cannot "
                            "renew it" % (snap.default_target, left / 3600))
    except Exception as exc:                                  # noqa: BLE001 - cannot judge: refuse
        problems.append("register unreadable: %s" % type(exc).__name__)
    return problems


def remove(*, root="/", run: Runner = _run, stand: str = STAND, preflight: bool = True, **quiesce_kw) -> dict:
    """The rollback removal. Refused (nothing touched but the stopped renewal timer) unless `quiesce`
    finds no running renewal, no held lock, no open head-only operation and >= 48 h on the default.
    Lock files are never unlinked (a held inode would split the lock); the register is never touched."""
    if preflight:
        problems = quiesce(stand=stand, run=run, **quiesce_kw)
        if problems:
            return {"removed": False, "left": ["PREFLIGHT " + p for p in problems]}
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
    _renew_state(root, stand).unlink(missing_ok=True)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "reset-failed"] + list(UNITS))
    left = [str(p) for p in [etc / n for n in UNITS] + [etc / DROPIN_DIR / DROPIN] + _stamps(root) + [_state(root)]
            + [_renew_state(root, stand)] if p.exists()]
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
    sub.add_parser("preflight")
    args = p.parse_args(argv)
    stand = os.environ.get("TRF_STAND", STAND)
    if args.cmd == "preflight":
        problems = quiesce(stand=stand)
        for line in problems:
            print("PREFLIGHT %s" % line)
        print("TRUST_TIMERS_%s" % ("QUIESCENT" if not problems else "NOT_QUIESCENT"))
        return 0 if not problems else 1
    if args.cmd == "install":
        done = install(args.release_dir, stand=stand)
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
    done = remove(stand=stand)
    for line in done["left"]:
        print("LEFT %s" % line)
    print("TRUST_TIMERS_%s" % ("REMOVED" if done["removed"] else "REMOVE_INCOMPLETE"))
    return 0 if done["removed"] else 1


__all__ = ["DROPIN", "DROPIN_DIR", "SCHEDULE", "TIMERS", "UNITS", "calendar_elapses", "check", "install", "main",
           "parse_unit", "remove", "unit_problems"]


if __name__ == "__main__":
    sys.exit(main())
