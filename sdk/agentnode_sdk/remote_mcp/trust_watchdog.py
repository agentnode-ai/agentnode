"""The independent trust watchdog (trust-refresh C4, C5, C13, C14; DEFECT-LIVE-001).

The trust refresh (`rmcp-trust-refresh.timer` -> `targets_cli trust-refresh`) is what keeps every
execution target's revocation and tombstone lists fresh, and its own failure is what raised the
alert -- so when its timer stopped being scheduled (the live defect: active, NEXT=infinity) nothing
ran and nothing alerted, and seven days later every target run would have been refused. This
watchdog is the second, separate pair of eyes. It runs on its OWN hourly calendar timer, shares no
unit dependency with the refresh, and checks the RESULT, not the refresh:

  (a) the deployment's revocation list and tombstone list: readable, signed by the deployment's
      anchor, valid at the effective time (clock and durable floor, as the TLS door judges them),
      and with at least 48 hours left measured from the later of the two;
  (b) every live target in the register: an operator-signed acknowledgement EXACTLY equal to the
      deployment's pair (number, both digests, every validity time), at most 13 hours old;
  (c) the refresh timer: loaded, enabled, active, and a FINITE next elapse in the future within one
      period of the schedule -- `active` with no next elapse is the defect itself, and red;
  (d) the attestations (attest-renewal D8): every active target's record valid with more than its class
      warning left (48 h managed, 6 h customer) at the effective, pinned time; the default target active;
      the renewal timer finite within 6 h 10 min; the renewal state present, its last run at most
      6 h 40 min old, no target degraded or blocked, and the record digest it last published equal to the
      register's; no renew/probe operation open for 30 minutes (lock holders read from /proc/locks,
      never by taking a lock).

It is read-only: the operator PUBLIC key only, no private key, no issuer lock, no worker connection,
no publication, no write to the register (its pinned clock included), acknowledgements, floors or
ledgers. It writes one thing, its own state file (atomically, 0644), and failing to write it is red.
Red = exit 1 with every problem named (codes, numbers, short digests, times, target ids -- never
key or token material); the unit then fails and `OnFailure=rmcp-alert@` records a persistent alert.
Every hourly run while red fails again.

`timer-check` is the same timer judgement on its own, for the installer (`trust-timers.sh`) and for
the existing daily certificate check, which watches THIS watchdog's timer (Codex D4: the refresh is
never made responsible for the watchdog's liveness).

  python -m agentnode_sdk.remote_mcp.trust_watchdog check --stand S [--timer U] [--state-file F]
  python -m agentnode_sdk.remote_mcp.trust_watchdog timer-check --unit U --bound-seconds N
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

REFRESH_TIMER = "rmcp-trust-refresh.timer"
WATCHDOG_TIMER = "rmcp-trust-watchdog.timer"
#: The attestation renewal (attest-renewal): its timer, how far its next run may lie, how old its last
#: finished run may be (one period + its timeout + slack).
RENEW_TIMER = "rmcp-attest-renew.timer"
RENEW_BOUND_SECONDS = 6 * 3600 + 600
RENEW_RUN_MAX_AGE_SECONDS = 6 * 3600 + 2400
#: Red below this much validity left on either list.
MARGIN_SECONDS = 48 * 3600
#: Red when a live target's acknowledgement is older: two refresh periods and an hour.
ACK_MAX_AGE_SECONDS = 13 * 3600
#: How far ahead a timer's next elapse may lie: the period + RandomizedDelaySec + AccuracySec + slack.
REFRESH_BOUND_SECONDS = 6 * 3600 + 600
WATCHDOG_BOUND_SECONDS = 3600 + 300
STATE_FILE = "/var/lib/agentnode-mcp/alerts/trust-watchdog.json"
SHOW_PROPERTIES = ("LoadState", "ActiveState", "UnitFileState", "NextElapseUSecMonotonic", "LastTriggerUSec")

Runner = Callable[[list], str]


def _run(argv: list) -> str:
    env = dict(os.environ, LC_ALL="C", TZ="UTC")
    return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True,
                          env=env, timeout=60).stdout


# ---------------------------------------------------------------------- timers
def timer_state(unit: str, *, run: Runner = _run) -> dict:
    """What systemd says about a timer: `systemctl show` for its states, and `list-timers -o json`
    for the next elapse in microseconds since the epoch (`show` prints it only as local text)."""
    state: dict[str, Any] = {"unit": unit}
    for line in run(["systemctl", "show", unit, "-p", ",".join(SHOW_PROPERTIES)]).splitlines():
        key, sep, value = line.partition("=")
        if sep and key in SHOW_PROPERTIES:
            state[key] = value.strip()
    state["next_usec"] = None
    try:
        rows = json.loads(run(["systemctl", "list-timers", "--all", "-o", "json", unit]) or "[]")
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("unit") == unit:
                state["next_usec"] = row.get("next")
    except ValueError:
        state["next_usec"] = "unparsable"
    return state


def next_elapse(state: dict) -> float | None:
    """The next elapse in seconds since the epoch, or None when there is none that is finite."""
    v = state.get("next_usec")
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        return None
    return v / 1_000_000.0


def timer_problems(state: dict, *, now: float, bound_seconds: float) -> list[str]:
    """Every reason the timer will not fire in time. Active with no finite next elapse is red."""
    unit = state.get("unit", "?")
    out = []
    if state.get("LoadState") != "loaded":
        out.append("timer_not_loaded %s (%s)" % (unit, state.get("LoadState") or "unknown"))
    if state.get("UnitFileState") != "enabled":
        out.append("timer_not_enabled %s (%s)" % (unit, state.get("UnitFileState") or "unknown"))
    if state.get("ActiveState") != "active":
        out.append("timer_not_active %s (%s)" % (unit, state.get("ActiveState") or "unknown"))
    nxt = next_elapse(state)
    if nxt is None:
        out.append("timer_next_not_finite %s (next=%r monotonic=%r)"
                   % (unit, state.get("next_usec"), state.get("NextElapseUSecMonotonic")))
    elif nxt <= now:
        out.append("timer_next_in_past %s (%s)" % (unit, _iso(nxt)))
    elif nxt > now + bound_seconds:
        out.append("timer_next_too_far %s (%s, bound %d s)" % (unit, _iso(nxt), int(bound_seconds)))
    return out


# ---------------------------------------------------------------------- trust
def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def trust_problems(stand, *, now: float, view=None, tls=None) -> tuple[list[str], dict]:
    """(a) and (b): the deployment pair at the effective time, and every live target's exact ack."""
    from agentnode_sdk.pki import revocation as _revocation
    from agentnode_sdk.pki import tombstones as _tombstones
    from agentnode_sdk.remote_mcp import targets as T
    from agentnode_sdk.remote_mcp.target_ops import LIVE
    from agentnode_sdk.worker import trust_install

    problems: list[str] = []
    facts: dict[str, Any] = {}
    if tls is None:
        from agentnode_sdk.remote_mcp.server import _worker_base
        _cfg, tls = _worker_base(str(stand))
    view = view or tls.trust("gateway")
    try:
        crl = Path(tls.revocation_list).read_bytes()
        tombs = Path(tls.identity_tombstones).read_bytes()
    except OSError as exc:
        return ["trust_files_unreadable (%s)" % type(exc).__name__], facts
    try:
        effective = float(view.effective_time())
        anchor = view.anchor()
    except Exception as exc:                                  # noqa: BLE001 - the door would refuse too
        return ["effective_time_or_anchor_unusable (%s)" % type(exc).__name__], facts
    ref = max(now, effective)
    try:
        rl = _revocation.read(crl, anchor, effective)
        facts["list_number"] = rl.number
        facts["list_next_update"] = _iso(rl.next_update)
        if rl.this_update > ref + trust_install.MAX_SKEW_SECONDS:
            problems.append("list_not_yet_valid (thisUpdate %s)" % _iso(rl.this_update))
        if rl.next_update - ref < MARGIN_SECONDS:
            problems.append("list_expires_within_48h (nextUpdate %s, %d s left)" % (_iso(rl.next_update),
                                                                                   int(rl.next_update - ref)))
    except _revocation.ListUnusable as exc:
        problems.append("list_unusable (%s)" % exc.check)
    try:
        tb = _tombstones.read(tombs, deployment=tls.deployment, verifier=view._verify_with_anchor)
        tb.usable_at(effective)
        facts["tombstones_not_after"] = _iso(tb.not_after)
        if tb.published_at > ref + trust_install.MAX_SKEW_SECONDS:
            problems.append("tombstones_not_yet_valid (published %s)" % _iso(tb.published_at))
        if tb.not_after - ref < MARGIN_SECONDS:
            problems.append("tombstones_expire_within_48h (not_after %s, %d s left)" % (_iso(tb.not_after),
                                                                                       int(tb.not_after - ref)))
    except _tombstones.ListUnusable as exc:
        problems.append("tombstones_unusable (%s)" % exc.reason)
    try:
        pair = trust_install.facts_of(crl, tombs)
        facts["list_sha256"] = pair["revocation_list_sha256"][:16]
        facts["tombstones_sha256"] = pair["tombstones_sha256"][:16]
    except ValueError:
        pair = None
        problems.append("pair_unparsable")

    op_pub = bytes.fromhex(io.open(os.path.join(str(stand), "operator_pub.hex"), encoding="utf-8").read().strip())
    try:
        snap = T.Register(stand, op_pub, pin_clock=False).load()
        acks = T.TrustAcks(stand, op_pub).load()
    except T.TargetsUnavailable as exc:
        return problems + ["register_or_acks_unavailable (%s)" % exc], facts
    live = sorted(t.target_id for t in snap.targets.values() if t.effective_status(snap.now) in LIVE)
    facts["live_targets"] = live
    facts["register_generation"] = snap.generation
    for tid in sorted(set(acks) - set(snap.targets)):
        problems.append("ack_unknown_target %s" % tid)      # a register keeps every target, terminal ones too
    for tid in live:
        ack = acks.get(tid)
        if not ack:
            problems.append("ack_missing %s" % tid)
            continue
        wrong = trust_install.differences(ack, pair)
        if wrong:
            problems.append("ack_mismatch %s (%s)" % (tid, ",".join(wrong)))
        try:
            age = now - float(ack["acked_at"])
        except (KeyError, TypeError, ValueError):
            problems.append("ack_time_missing %s" % tid)
            continue
        if age > ACK_MAX_AGE_SECONDS:
            problems.append("ack_stale %s (%d s old)" % (tid, int(age)))
    return problems, facts


# ---------------------------------------------------------------------- attestations (attest-renewal D8)
def held_flock_inodes() -> set | None:
    """(device, inode) of every file an flock is held on, from /proc/locks -- READ, never a lock attempt,
    so inspecting cannot take, release or delay a probe's lock. None where the kernel cannot say."""
    try:
        lines = Path("/proc/locks").read_text().splitlines()
    except OSError:
        return None
    out = set()
    for line in lines:
        parts = line.split()
        if "FLOCK" not in parts:
            continue
        for p in parts:
            bits = p.split(":")
            if len(bits) == 3 and all(bits):
                try:
                    out.add((os.makedev(int(bits[0], 16), int(bits[1], 16)), int(bits[2])))
                except ValueError:
                    pass
    return out


def _lock_held(path: Path, held: set | None) -> str:
    try:
        st = os.stat(path)
    except OSError:
        return "no lock file"
    if held is None:
        return "lock state unknown"
    return "lock held" if (st.st_dev, st.st_ino) in held else "lock not held"


def attestation_problems(stand, *, now: float, run: Runner = _run, state_path=None,
                         renew_timer: str = RENEW_TIMER) -> tuple[list[str], dict]:
    """Every active target's record valid with more than its class warning left (effective, pinned time);
    the default target active; the renewal timer finite; the renewal state present, recent and agreeing
    with the register; no target degraded or blocked; no renew/probe operation open for 30 minutes."""
    from agentnode_sdk.remote_mcp import targets as T
    from agentnode_sdk.remote_mcp import target_ops as O

    problems: list[str] = []
    facts: dict[str, Any] = {}
    tstate = timer_state(renew_timer, run=run)
    problems += timer_problems(tstate, now=now, bound_seconds=RENEW_BOUND_SECONDS)
    nxt = next_elapse(tstate)
    facts["renew_next"] = _iso(nxt) if nxt else None
    op_pub = bytes.fromhex(io.open(os.path.join(str(stand), "operator_pub.hex"), encoding="utf-8").read().strip())
    try:
        snap = T.Register(stand, op_pub, pin_clock=False).load()
    except T.TargetsUnavailable as exc:
        return problems + ["register_unavailable (%s)" % exc], facts
    eff = max(now, snap.now)                                   # the later of clock and pinned high-water
    default = snap.target(snap.default_target)
    if default is None or default.effective_status(eff) != T.ACTIVE:
        problems.append("default_target_not_active %s (%s)" % (snap.default_target,
                                                              default.effective_status(eff) if default else "missing"))
    active = [t for t in snap.targets.values() if t.effective_status(eff) == T.ACTIVE]
    facts["attestations"] = {}
    for t in sorted(active, key=lambda t: t.target_id):
        a = t.attestation or {}
        pol = O.RENEW_POLICY.get(t.assurance)
        if not a or not t.attestation_valid(eff):
            problems.append("attestation_invalid %s" % t.target_id)
            continue
        left = float(a["expires_at"]) - eff
        facts["attestations"][t.target_id] = {"expires_at": _iso(float(a["expires_at"])), "hours_left": round(left / 3600, 1),
                                              "record": a["record_digest"][:16]}
        if pol is None or left <= pol["warn"]:
            problems.append("attestation_reserve_low %s (%.1f h left, warning at %d h)"
                            % (t.target_id, left / 3600, (pol or {"warn": 0})["warn"] // 3600))
    path = Path(state_path) if state_path is not None else O.renew_state_path(stand)
    try:
        state = O.load_renew_state(path)
    except ValueError as exc:
        state = None
        problems.append("renew_state_unreadable (%s)" % exc)
    else:
        if state is None:
            problems.append("renew_state_missing")
    if state is not None:
        entries = state["targets"]
        for t in active:
            e = entries.get(t.target_id)
            if not e:
                problems.append("renew_state_missing %s" % t.target_id)
            elif e.get("published_record_digest") != (t.attestation or {}).get("record_digest"):
                problems.append("register_inconsistent %s (renewal published %s, register holds %s)"
                                % (t.target_id, str(e.get("published_record_digest"))[:16],
                                   (t.attestation or {}).get("record_digest", "")[:16]))
        for tid, e in sorted(entries.items()):
            t = snap.target(tid)
            if t is None or t.effective_status(eff) in T.TERMINAL:
                continue
            if e.get("result") in ("degraded", "blocked", "busy"):
                problems.append("renew_%s %s (%s)" % (e["result"], tid, str(e.get("reason", ""))[:120]))
        last = state.get("last_run") or {}
        since = float(last.get("finished_at") or state.get("bootstrapped_at") or 0)
        if now - since > RENEW_RUN_MAX_AGE_SECONDS:
            problems.append("renew_run_overdue (last %s)" % (_iso(since) if since else "never"))
        facts["renew_last_run"] = {"finished_at": _iso(since) if since else None, "status": last.get("status")}
    ops_dir = Path(stand) / "targets" / "ops"
    held = held_flock_inodes()
    for f in sorted(ops_dir.glob("op-*.json")) if ops_dir.is_dir() else []:
        try:
            op = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            problems.append("operation_unreadable %s" % f.name)
            continue
        if op.get("state") != "open" or op.get("kind") not in ("renew", "probe"):
            continue
        age = now - float(op.get("created_at") or 0)
        if age > O.HUNG_AFTER_SECONDS:
            lock = Path(str(ops_dir.parent / (O.PROBE_LOCK_PREFIX + str(op.get("target_id")))) + ".lock")
            problems.append("probe_hung %s %s %s (%d s open, %s)" % (op.get("op_id"), op.get("kind"),
                                                                    op.get("target_id"), int(age), _lock_held(lock, held)))
    return problems, facts


# ---------------------------------------------------------------------- state
def write_state(path, doc: dict) -> None:
    """Atomically: a temporary file in the same directory, fsync, rename, directory fsync; 0644."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, sort_keys=True, indent=1)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(str(tmp), 0o644)
    os.replace(str(tmp), str(path))
    if hasattr(os, "O_DIRECTORY"):
        dfd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def check(stand, *, now: float | None = None, timer: str = REFRESH_TIMER, state_file=STATE_FILE,
          run: Runner = _run, tls=None, view=None, renew_state_file=None, renew_timer: str = RENEW_TIMER) -> dict:
    """One watchdog run. Returns the report that was written; `report["state"]` is green or red."""
    now = time.time() if now is None else float(now)
    try:
        problems, facts = trust_problems(stand, now=now, tls=tls, view=view)
    except Exception as exc:                                  # noqa: BLE001 - an unknown failure is red
        problems, facts = ["watchdog_error (%s)" % type(exc).__name__], {}
    tstate = timer_state(timer, run=run)
    problems += timer_problems(tstate, now=now, bound_seconds=REFRESH_BOUND_SECONDS)
    nxt = next_elapse(tstate)
    facts["refresh_next"] = _iso(nxt) if nxt else None
    try:
        more, afacts = attestation_problems(stand, now=now, run=run, state_path=renew_state_file,
                                            renew_timer=renew_timer)
    except Exception as exc:                                  # noqa: BLE001 - an unknown failure is red
        more, afacts = ["attestation_watchdog_error (%s)" % type(exc).__name__], {}
    problems += more
    facts.update(afacts)
    report = {"state": "red" if problems else "green", "checked_at": _iso(now), "problems": problems,
              "facts": facts}
    try:
        write_state(state_file, report)
    except OSError as exc:
        report["problems"].append("state_file_unwritable (%s)" % type(exc).__name__)
        report["state"] = "red"
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="rmcp-trust-watchdog")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--stand", required=True)
    c.add_argument("--timer", default=REFRESH_TIMER)
    c.add_argument("--state-file", default=STATE_FILE)
    c.add_argument("--renew-timer", default=RENEW_TIMER)
    c.add_argument("--renew-state-file", default=None)
    t = sub.add_parser("timer-check")
    t.add_argument("--unit", action="append", required=True)
    t.add_argument("--bound-seconds", type=float, action="append", required=True)
    args = p.parse_args(argv)
    if args.cmd == "check":
        report = check(args.stand, timer=args.timer, state_file=args.state_file, renew_timer=args.renew_timer,
                       renew_state_file=args.renew_state_file)
        print(json.dumps(report, indent=1, sort_keys=True))
        return 0 if report["state"] == "green" else 1
    if len(args.unit) != len(args.bound_seconds):
        p.error("one --bound-seconds per --unit")
    now = time.time()
    problems = []
    for unit, bound in zip(args.unit, args.bound_seconds):
        st = timer_state(unit)
        found = timer_problems(st, now=now, bound_seconds=bound)
        nxt = next_elapse(st)
        print("%s %s next=%s" % ("RED" if found else "OK", unit, _iso(nxt) if nxt else "none"))
        problems += found
    for line in problems:
        print("ALERT %s" % line)
    return 1 if problems else 0


__all__ = ["ACK_MAX_AGE_SECONDS", "MARGIN_SECONDS", "REFRESH_BOUND_SECONDS", "REFRESH_TIMER", "RENEW_BOUND_SECONDS",
           "RENEW_RUN_MAX_AGE_SECONDS", "RENEW_TIMER", "STATE_FILE", "WATCHDOG_BOUND_SECONDS", "WATCHDOG_TIMER",
           "attestation_problems", "check", "held_flock_inodes", "main", "next_elapse", "timer_problems",
           "timer_state", "trust_problems", "write_state"]


if __name__ == "__main__":
    sys.exit(main())
