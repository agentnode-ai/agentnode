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
#: trust-floor D3: the publication authority's 5-minute cycle, and the control floor run brought into the release.
CYCLE_SERVICE, CYCLE_TIMER = "rmcp-trust-cycle.service", W.CYCLE_TIMER
FLOOR_SERVICE, FLOOR_TIMER = "rmcp-control-floor.service", W.FLOOR_TIMER
UNITS = (REFRESH_SERVICE, REFRESH_TIMER, WATCHDOG_SERVICE, WATCHDOG_TIMER, RENEW_SERVICE, RENEW_TIMER,
         CYCLE_SERVICE, CYCLE_TIMER, FLOOR_SERVICE, FLOOR_TIMER)
TIMERS = ((REFRESH_TIMER, W.REFRESH_BOUND_SECONDS), (WATCHDOG_TIMER, W.WATCHDOG_BOUND_SECONDS),
          (RENEW_TIMER, W.RENEW_BOUND_SECONDS), (CYCLE_TIMER, W.FIVE_MINUTE_BOUND_SECONDS),
          (FLOOR_TIMER, W.FIVE_MINUTE_BOUND_SECONDS))
#: The floor units the release replaces: saved byte-exact with their metadata before, restored on rollback.
FLOOR_UNITS = (FLOOR_SERVICE, FLOOR_TIMER)
LEGACY_DIR = "var/lib/agentnode-mcp/trust-floor-legacy"
LEGACY_MANIFEST_SCHEMA = "agentnode.trust-floor-legacy.v1"
#: The hand-installed script the legacy floor service runs: never modified, its digest proven unchanged.
LEGACY_SCRIPT = "opt/agentnode/scripts/control_floor_advance.py"
#: The 5-minute timers' required accuracy, and the floor service's time bound (Codex C4/C5).
FIVE_MINUTE_ACCURACY_SECONDS = 15
FLOOR_TIMEOUT_SECONDS = 60
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
            RENEW_TIMER: [(h, 30, 0) for h in (3, 9, 15, 21)],
            CYCLE_TIMER: [(h, m, 0) for h in range(24) for m in range(0, 60, 5)],
            FLOOR_TIMER: [(h, m, 0) for h in range(24) for m in range(0, 60, 5)]}

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


def _seconds(value: str) -> float | None:
    """A systemd time span of the forms this policy writes (`15s`, `60`, `4min`, `1min 30s`); None otherwise."""
    total, found = 0.0, False
    for part in str(value).split():
        m = re.fullmatch(r"(\d+(?:\.\d+)?)(us|ms|s|sec|m|min|h)?", part)
        if not m:
            return None
        n, u = float(m.group(1)), m.group(2) or "s"
        total += n * {"us": 1e-6, "ms": 1e-3, "s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600}[u]
        found = True
    return total if found else None


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
        if name in (CYCLE_TIMER, FLOOR_TIMER):
            acc = [v for k, v in timer if k == "AccuracySec"]
            secs = _seconds(acc[-1]) if acc else None
            if secs is None or secs > FIVE_MINUTE_ACCURACY_SECONDS:
                out.append("%s: AccuracySec must be at most %ds (%r)" % (name, FIVE_MINUTE_ACCURACY_SECONDS,
                                                                        acc[-1] if acc else None))
        if not any(k == "WantedBy" and v == "timers.target" for k, v in units.get("Install", [])):
            out.append("%s: not WantedBy=timers.target" % name)
    if name in (WATCHDOG_SERVICE, WATCHDOG_TIMER, DROPIN):
        for section in units.values():
            for k, v in section:
                if k in DEPENDENCIES and any(w in v for w in ("rmcp-trust-refresh", "rmcp-attest-renew",
                                                              "rmcp-trust-cycle", "rmcp-control-floor")):
                    out.append("%s: depends on what it watches (%s=%s)" % (name, k, v))
    if name == RENEW_SERVICE:
        unit, service = dict(units.get("Unit", [])), units.get("Service", [])
        if unit.get("OnFailure") != "rmcp-alert@%n.service":
            out.append("%s: no OnFailure=rmcp-alert@%%n.service" % name)
        if not any(k == "ExecStart" and "targets_cli" in v and "attest-renew" in v for k, v in service):
            out.append("%s: does not run targets_cli attest-renew" % name)
        if not any(k == "TimeoutStartSec" for k, _ in service):
            out.append("%s: no TimeoutStartSec (a hung renewal must end)" % name)
    if name in (CYCLE_SERVICE, REFRESH_SERVICE):
        unit, service = dict(units.get("Unit", [])), units.get("Service", [])
        verb = "trust-cycle" if name == CYCLE_SERVICE else "trust-refresh"
        if unit.get("OnFailure") != "rmcp-alert@%n.service":
            out.append("%s: no OnFailure=rmcp-alert@%%n.service" % name)
        if not any(k == "ExecStart" and "targets_cli" in v and v.rstrip().endswith(verb) for k, v in service):
            out.append("%s: does not run targets_cli %s" % (name, verb))
        if not any(k == "TimeoutStartSec" for k, _ in service):
            out.append("%s: no TimeoutStartSec (a hung distribution must end)" % name)
    if name == FLOOR_SERVICE:
        unit, service = dict(units.get("Unit", [])), units.get("Service", [])
        execs = [v for k, v in service if k == "ExecStart"]
        if unit.get("OnFailure") != "rmcp-alert@%n.service":
            out.append("%s: no OnFailure=rmcp-alert@%%n.service" % name)
        if len(execs) != 1 or "agentnode_sdk.remote_mcp.control_floor" not in execs[0]:
            out.append("%s: does not run exactly the control floor module" % name)
        if any("targets_cli" in v or "trust-cycle" in v or "trust-refresh" in v for v in execs):
            out.append("%s: the floor run must not distribute" % name)
        timeout = [_seconds(v) for k, v in service if k == "TimeoutStartSec"]
        if not timeout or timeout[-1] is None or timeout[-1] > FLOOR_TIMEOUT_SECONDS:
            out.append("%s: TimeoutStartSec must be at most %ds" % (name, FLOOR_TIMEOUT_SECONDS))
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


def _meta(path: Path) -> dict:
    import hashlib
    import stat as _stat
    st = os.lstat(path)
    kind = "file" if _stat.S_ISREG(st.st_mode) else ("symlink" if _stat.S_ISLNK(st.st_mode) else "other")
    out = {"type": kind, "mode": "%04o" % _stat.S_IMODE(st.st_mode), "uid": st.st_uid, "gid": st.st_gid}
    if kind == "file":
        out["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def legacy_dir(root) -> Path:
    return Path(root) / LEGACY_DIR


def backup_legacy_floor(release_files: dict, *, root="/") -> str:
    """Save the floor units the release is about to replace -- bytes, type, mode, owner, digest -- unless a
    backup exists already (a reinstall never overwrites the original) or the units on disk ARE the release's
    (nothing foreign to keep). Returns what it did."""
    d = legacy_dir(root)
    if (d / "manifest.json").exists():
        return "kept existing backup"
    etc = _etc(root)
    present = [n for n in FLOOR_UNITS if (etc / n).exists() or (etc / n).is_symlink()]
    if present and all((etc / n).is_file() and (etc / n).read_bytes() == release_files[n].read_bytes()
                       for n in FLOOR_UNITS if n in present):
        return "units on disk are the release's: nothing to back up"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(str(d), 0o700)
    manifest: dict = {"schema": LEGACY_MANIFEST_SCHEMA, "units": {}, "script": None, "saved_at": round(time.time(), 3)}
    for n in FLOOR_UNITS:
        p = etc / n
        if not (p.exists() or p.is_symlink()):
            manifest["units"][n] = {"absent": True}
            continue
        m = _meta(p)
        if m["type"] != "file":
            raise RuntimeError("%s is a %s, not a file: refusing to replace what cannot be restored exactly" % (p, m["type"]))
        _replace(p, d / n)
        manifest["units"][n] = m
    script = Path(root) / LEGACY_SCRIPT
    manifest["script"] = _meta(script) if script.exists() else {"absent": True}
    data = (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode("utf-8")
    tmp = d / ".manifest.json.new"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp), str(d / "manifest.json"))
    return "backed up %s" % ",".join(n for n in FLOOR_UNITS if not manifest["units"][n].get("absent"))


def restore_legacy_floor(*, root="/", run: Runner = _run) -> list[str]:
    """Rollback: put the saved floor units back with their exact bytes, mode and owner, prove it (digests and
    metadata equal the manifest, the legacy script unchanged), then enable and start the legacy timer.
    Returns every difference left ([] = restored exactly). Without a backup: [] only if no floor unit exists."""
    d = legacy_dir(root)
    etc = _etc(root)
    try:
        manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        if not any((etc / n).exists() or (etc / n).is_symlink() for n in FLOOR_UNITS):
            return []                                         # no floor units here at all: nothing to restore
        return ["no legacy floor backup to restore"]
    except (OSError, ValueError) as exc:
        return ["legacy floor backup unreadable (%s)" % type(exc).__name__]
    left = []
    if (manifest["units"].get(FLOOR_TIMER) or {}).get("absent"):
        run(["systemctl", "disable", "--now", FLOOR_TIMER])   # there was none before: nothing may be left enabled
        (Path(root) / "var" / "lib" / "systemd" / "timers" / ("stamp-" + FLOOR_TIMER)).unlink(missing_ok=True)
    for n in FLOOR_UNITS:
        want = manifest["units"].get(n) or {"absent": True}
        dest = etc / n
        if want.get("absent"):
            dest.unlink(missing_ok=True)
            continue
        _replace(d / n, dest)
        os.chmod(str(dest), int(want["mode"], 8))
        try:
            os.chown(str(dest), int(want["uid"]), int(want["gid"]))
        except (AttributeError, PermissionError, OSError):
            pass                                              # compared below: a difference is reported, not hidden
        got = _meta(dest)
        if got != {k: want[k] for k in ("type", "mode", "uid", "gid", "sha256")}:
            left.append("%s restored as %s, saved %s" % (n, got, want))
    script = Path(root) / LEGACY_SCRIPT
    want = manifest.get("script") or {"absent": True}
    got = {"absent": True} if not script.exists() else _meta(script)
    if got != want:
        left.append("legacy script %s changed: %s, saved %s" % (LEGACY_SCRIPT, got, want))
    run(["systemctl", "daemon-reload"])
    if not manifest["units"].get(FLOOR_TIMER, {}).get("absent"):
        rc, said = run(["systemctl", "enable", "--now", FLOOR_TIMER])
        if rc != 0:
            left.append("legacy floor timer not started: %s" % said.strip()[-200:])
    if not left:
        for n in FLOOR_UNITS:
            (d / n).unlink(missing_ok=True)
        (d / "manifest.json").unlink(missing_ok=True)
        try:
            d.rmdir()
        except OSError:
            pass
    return left


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
    # trust-floor R3: a predecessor the parent saved (legacy files) becomes the pointer's generation first
    rc, said = run(["env", "PYTHONPATH=%s" % (Path(release_dir) / "sdk"), sys.executable, "-m",
                    "agentnode_sdk.remote_mcp.targets_cli", "--stand", stand, "trust-import-legacy"])
    if rc != 0:
        return {"installed": False, "stage": "import-legacy", "problems": [said.strip()[-300:]]}
    try:
        backup_legacy_floor(files, root=root)
    except (OSError, RuntimeError) as exc:
        return {"installed": False, "stage": "floor-backup", "problems": [str(exc)[-300:]]}
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


def _live_trust_refusals(stand: str, now: float) -> list[str]:
    """Live targets the router would refuse NOW on trust (`targets.trust_current` with this control's own
    deployment pair and verified predecessor) -- a distribution, of a security change above all, unresolved."""
    from agentnode_sdk.remote_mcp import server
    from agentnode_sdk.remote_mcp import targets as T
    from agentnode_sdk.remote_mcp.target_ops import LIVE
    op_pub = bytes.fromhex(Path(stand, "operator_pub.hex").read_text(encoding="utf-8").strip())
    snap = T.Register(stand, op_pub, pin_clock=False).load()
    acks = T.TrustAcks(stand, op_pub).load()
    dep, prev = server.deployment_trust(stand)(), server.previous_trust(stand)()
    return [t.target_id for t in snap.targets.values()
            if t.effective_status(snap.now) in LIVE
            and not T.trust_current(acks.get(t.target_id), now=now, deployment=dep, previous=prev)]


def publication_problems(stand: str, *, now: float, trust_check: Callable[[str, float], list] | None = None) -> list[str]:
    """Rollback gate (trust-floor C22): no open publication or distribution record, and no live target the
    router refuses on trust -- the parent could not finish either."""
    from agentnode_sdk.remote_mcp import trust_publication as TP
    out = []
    try:
        for r in TP.load_records(stand):
            if r["state"] == TP.OPEN:
                out.append("open trust %s record %s (%s): let `targets_cli trust-refresh` finish it first"
                           % (r["kind"], r["record_id"], r.get("phase")))
    except ValueError as exc:
        out.append("trust publication journal unreadable: %s" % exc)
    try:
        refused = (trust_check or _live_trust_refusals)(stand, now)
    except Exception as exc:                                  # noqa: BLE001 - cannot judge: refuse
        refused = ["trust state unreadable: %s" % type(exc).__name__]
    for tid in refused:
        out.append("unresolved trust distribution: %s is refused on trust" % tid)
    return out


def quiesce(*, stand: str = STAND, run: Runner = _run, now: Callable[[], float] = time.time,
            sleep: Callable[[float], None] = time.sleep, wait_seconds: float = QUIESCE_WAIT_SECONDS,
            trust_check: Callable[[str, float], list] | None = None) -> list[str]:
    """Rollback preflight (attest-renewal D10): stop scheduling renewals, wait for a running renewal to end,
    then refuse while any probe/renewal lock is held, any head-only operation is open, or the managed
    default has less than 48 h of validity left (the older release cannot renew it). [] = quiescent."""
    from agentnode_sdk.remote_mcp import target_ops as O
    from agentnode_sdk.remote_mcp import targets as T

    problems: list[str] = []
    # the renewal and the publication authority's runs stop being scheduled; the floor run (local) goes on
    run(["systemctl", "disable", "--now", RENEW_TIMER, CYCLE_TIMER, REFRESH_TIMER])
    deadline = now() + wait_seconds
    for service in (RENEW_SERVICE, CYCLE_SERVICE, REFRESH_SERVICE):
        while True:
            _rc, said = run(["systemctl", "show", service, "-p", "ActiveState"])
            if "ActiveState=activating" not in said and "ActiveState=active" not in said:
                break
            if now() >= deadline:
                problems.append("%s still running after %d s" % (service, int(wait_seconds)))
                break
            sleep(5)
    problems += publication_problems(stand, now=now(), trust_check=trust_check)
    tdir = Path(stand) / "targets"
    held = W.held_flock_inodes()
    from agentnode_sdk.remote_mcp import trust_publication as TP
    locks = sorted(tdir.glob(O.PROBE_LOCK_PREFIX + "*.lock")) + [Path(str(tdir / O.RENEW_LOCK_NAME) + ".lock"),
                                                                 Path(str(tdir / O.REFRESH_LOCK_NAME) + ".lock"),
                                                                 Path(str(tdir / TP.DISTRIBUTION_LOCK_NAME) + ".lock")]
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
    if problems:                                              # not rolling back: trust distribution goes on
        run(["systemctl", "enable", "--now", CYCLE_TIMER, REFRESH_TIMER])
    return problems


def remove(*, root="/", run: Runner = _run, stand: str = STAND, preflight: bool = True, **quiesce_kw) -> dict:
    """The rollback removal. Refused (nothing touched but the stopped renewal timer) unless `quiesce`
    finds no running renewal, no held lock, no open head-only operation and >= 48 h on the default.
    Lock files are never unlinked (a held inode would split the lock); the register is never touched."""
    if preflight:
        problems = quiesce(stand=stand, run=run, **quiesce_kw)
        if problems:
            return {"removed": False, "left": ["PREFLIGHT " + p for p in problems]}
    from agentnode_sdk.remote_mcp import trust_publication as TP
    left: list[str] = []
    # trust-floor D10: the parent reads only the legacy predecessor files -- write the pointer's generation there
    if TP.load_pointer_safely(stand) is not None:
        try:
            TP.materialize_legacy(stand)
        except (TP.Refused, OSError) as exc:
            return {"removed": False, "left": ["MATERIALIZE %s" % exc]}
    timers = [t for t, _ in TIMERS if t != FLOOR_TIMER]
    run(["systemctl", "disable", "--now"] + timers)
    etc = _etc(root)
    for name in UNITS:
        if name not in FLOOR_UNITS:
            (etc / name).unlink(missing_ok=True)
    (etc / DROPIN_DIR / DROPIN).unlink(missing_ok=True)
    try:
        (etc / DROPIN_DIR).rmdir()
    except OSError:
        pass                                                  # not empty or absent: someone else's drop-ins stay
    stamps = [s for s in _stamps(root) if not s.name.endswith(FLOOR_TIMER)]
    for stamp in stamps:
        stamp.unlink(missing_ok=True)
    _state(root).unlink(missing_ok=True)
    _renew_state(root, stand).unlink(missing_ok=True)
    _cycle_state(root, stand).unlink(missing_ok=True)
    # the floor units go back byte-exact (the floor never stops longer than this restart: 900 s budget)
    left += ["FLOOR " + p for p in restore_legacy_floor(root=root, run=run)]
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "reset-failed"] + [u for u in UNITS if u not in FLOOR_UNITS])
    left += [str(p) for p in [etc / n for n in UNITS if n not in FLOOR_UNITS] + [etc / DROPIN_DIR / DROPIN] + stamps
             + [_state(root)] + [_renew_state(root, stand), _cycle_state(root, stand)] if p.exists()]
    left += [str(p) for p in (etc / "timers.target.wants").glob("rmcp-trust-*")] if (etc / "timers.target.wants").exists() else []
    for t in timers:
        _rc, said = run(["systemctl", "show", t, "-p", "LoadState,ActiveState"])
        if "LoadState=not-found" not in said or "ActiveState=active" in said:
            left.append("%s still known to systemd (%s)" % (t, " ".join(said.split())))
    return {"removed": not left, "left": left}


def _cycle_state(root, stand) -> Path:
    from agentnode_sdk.remote_mcp.trust_publication import cycle_state_path
    if stand != STAND:
        return cycle_state_path(stand)
    return Path(root) / "var" / "lib" / "agentnode-mcp" / "alerts" / "trust-cycle.json"


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
