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
      deployment's pair (number, both digests, every validity time). Its age alone is no fault
      (trust-watchdog D2): an exact target is pushed again only at the daily rotation, so the age is judged
      on the PAIR -- `rotation_overdue` once the current list's thisUpdate is older than the cycle's own
      rotation age + 900 s with no live target behind and no publication open;
  (c) the refresh timer: loaded, enabled, active, and a FINITE next elapse in the future within one
      period of the schedule -- `active` with no next elapse is the defect itself, and red, unless the
      service the timer triggered is running right now within its own bound (trust-watchdog D1: systemd
      computes the next elapse only when that run ends), measured on the monotonic clock;
  (e) the publication authority (trust-floor C20/C21): the 5-minute cycle and floor timers finite, the journal
      readable with no record open longer than 10 minutes, the predecessor pointer readable and naming the
      generation its record saved, the last cycle successful within 15 minutes; a live target on the exact
      admissible predecessor is `ack_previous` (not red) for 10 minutes after the current pair's publication,
      `distribution_overdue` afterwards; any other difference stays `ack_mismatch` at once. Every live
      target reached within 15 minutes by the cycle's own non-mutating reachability check (trust-watchdog D3:
      an exact push or the router's handshake-only preflight), else `target_unreachable` with the router's
      classification. Until the first cycle after the cycle timer's activation has finished (an install, a
      forward switch, a restart, a reboot's catch-up), but at most 600 monotonic seconds, a missing cycle
      success or reach is `pending` and the report `initializing` -- never red, never green (D4).
  (d) the attestations (attest-renewal D8): every active target's record valid with more than its class
      warning left (48 h managed, 6 h customer) at the effective, pinned time; the default target active;
      the renewal timer finite within 6 h 10 min; the renewal state present, its last run at most
      6 h 40 min old, no target degraded or blocked, and the record digest it last published equal to the
      register's; no renew/probe operation open for 30 minutes (lock holders read from /proc/locks,
      never by taking a lock).

It is read-only: the operator PUBLIC key only, no private key, no issuer lock, no worker connection,
no publication, no write to the register (its pinned clock included), acknowledgements, floors or
ledgers. It writes its own state file (atomically, 0644), and failing to write it is red; and once per
red episode, on the first GREEN run after it, it APPENDS one line `<ts> rmcp-trust-watchdog recovered
(red since <ts>: <codes>)` to `alerts.log` beside it (trust-watchdog D6) -- one O_APPEND write, after the
state that says so is durable, never twice for one episode, never truncating, rewriting or rotating the
log; failing to append is red. Red = exit 1 with every problem named (codes, numbers, short digests,
times, target ids -- never key or token material); the unit then fails and `OnFailure=rmcp-alert@`
records a persistent alert. Every hourly run while red fails again. `initializing` exits 0.

`timer-check` is the same timer judgement on its own, for the installer (`trust-timers.sh`) and for
the existing daily certificate check, which watches THIS watchdog's timer (Codex D4: the refresh is
never made responsible for the watchdog's liveness).

  python -m agentnode_sdk.remote_mcp.trust_watchdog check --stand S [--timer U] [--state-file F]
  python -m agentnode_sdk.remote_mcp.trust_watchdog timer-check --unit U --bound-seconds N [--role R]
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
#: trust-floor: the publication authority's 5-minute cycle and the control floor run, and how far their next
#: elapse may lie (5 min + AccuracySec + slack); the last successful cycle may be at most this old.
CYCLE_TIMER = "rmcp-trust-cycle.timer"
FLOOR_TIMER = "rmcp-control-floor.timer"
FIVE_MINUTE_BOUND_SECONDS = 330
CYCLE_SUCCESS_MAX_AGE_SECONDS = 900
RENEW_BOUND_SECONDS = 6 * 3600 + 600
RENEW_RUN_MAX_AGE_SECONDS = 6 * 3600 + 2400
#: Red below this much validity left on either list.
MARGIN_SECONDS = 48 * 3600
#: How far ahead a timer's next elapse may lie: the period + RandomizedDelaySec + AccuracySec + slack.
REFRESH_BOUND_SECONDS = 6 * 3600 + 600
WATCHDOG_BOUND_SECONDS = 3600 + 300
STATE_FILE = "/var/lib/agentnode-mcp/alerts/trust-watchdog.json"
#: The persistent alert log `rmcp-alert@` appends to; the watchdog appends only its recovery line (D6).
ALERTS_LOG_NAME = "alerts.log"
SHOW_PROPERTIES = ("LoadState", "ActiveState", "UnitFileState", "NextElapseUSecMonotonic", "LastTriggerUSec",
                   "Triggers", "ActiveEnterTimestampMonotonic")
SERVICE_PROPERTIES = ("ActiveState", "SubState", "ExecMainStartTimestampMonotonic", "ExecMainExitTimestampMonotonic",
                      "ExecMainStatus", "Result")
#: trust-watchdog D1/R1: how long the service a timer triggered may run while the timer has no next elapse --
#: the service's own TimeoutStartSec + 60 s. Per ROLE, so units of another name and the same role are judged
#: alike; the unit policy (`trust_timers.unit_problems`) holds every such service to TimeoutStartSec <= bound - 60.
RUN_BOUND_SECONDS = {"refresh": 1260, "renew": 1860, "cycle": 300, "floor": 120, "watchdog": 180}
ROLES = {REFRESH_TIMER: "refresh", RENEW_TIMER: "renew", CYCLE_TIMER: "cycle", FLOOR_TIMER: "floor",
         WATCHDOG_TIMER: "watchdog"}
#: A service state in which its main process may be running (a oneshot is `activating` while it runs).
RUNNING_STATES = ("activating", "active", "deactivating")
#: trust-watchdog D2: a content-equal rotation is overdue this long after it became due (three cycle slots).
ROTATION_GRACE_SECONDS = 900
#: trust-watchdog D3: a live target must have been reached by the cycle within this long.
REACH_MAX_AGE_SECONDS = 900
#: trust-watchdog D4: how long after the cycle timer's activation a missing first cycle is `pending`:
#: one period (300) + AccuracySec (15) + the cycle's TimeoutStartSec (240) + 45.
INIT_GRACE_SECONDS = 600
#: trust-watchdog R3: a wall-clock age more negative than this is a clock that went back, named red.
CLOCK_REGRESSION_SECONDS = 300.0

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


def service_state(unit: str, *, run: Runner = _run) -> dict:
    """What systemd says about a service: its states and its main process's monotonic start/exit and result."""
    state: dict[str, Any] = {"unit": unit}
    for line in run(["systemctl", "show", unit, "-p", ",".join(SERVICE_PROPERTIES)]).splitlines():
        key, sep, value = line.partition("=")
        if sep and key in SERVICE_PROPERTIES:
            state[key] = value.strip()
    return state


def _usec(value) -> int | None:
    """A systemd monotonic timestamp in microseconds, or None when it is unset (0), infinite or malformed."""
    try:
        v = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def judge_timer(unit: str, *, role: str, now: float, bound_seconds: float, run: Runner = _run,
                monotonic: Callable[[], float] = time.monotonic, state: dict | None = None
                ) -> tuple[list[str], str | None, dict]:
    """trust-watchdog D1: the timer judgement of `timer_problems`, except that a loaded, enabled, active timer
    with no finite next elapse is NOT red while the one service it triggers (`<name>.service`, R1) is running
    and has run for at most `RUN_BOUND_SECONDS[role]` on the monotonic clock -- systemd computes the next elapse
    only when that run ends. One second over is `timer_service_overrunning`. A service that is not running gets
    one re-read of the timer (the run may have ended between the two reads): a valid finite next elapse then
    counts; otherwise `timer_next_not_finite` stands. Returns (problems, a `timer_running` note or None, the
    timer state finally judged)."""
    st = state if state is not None else timer_state(unit, run=run)
    problems = timer_problems(st, now=now, bound_seconds=bound_seconds)
    if not any(p.startswith("timer_next_not_finite ") for p in problems):
        return problems, None, st
    if any(not p.startswith("timer_next_not_finite ") for p in problems):
        return problems, None, st                             # not loaded/enabled/active: nothing explains it
    if st.get("next_usec") is not None:
        return problems, None, st                             # a next elapse that is there but malformed: red
    expected = unit[:-len(".timer")] + ".service" if unit.endswith(".timer") else ""
    triggers = (st.get("Triggers") or "").split()
    if not expected or not triggers:
        return problems, None, st                             # nothing named that could be running: red as it is
    if triggers != [expected]:
        return problems + ["timer_trigger_unexpected %s (%s)" % (unit, " ".join(triggers))], None, st
    svc = service_state(expected, run=run)
    start = _usec(svc.get("ExecMainStartTimestampMonotonic"))
    bound = RUN_BOUND_SECONDS[role]
    if svc.get("ActiveState") in RUNNING_STATES and start is not None:
        age = monotonic() - start / 1_000_000.0
        if 0 <= age <= bound:
            return [], "timer_running %s (%s %d s)" % (unit, expected, int(age)), st
        if age > bound:
            return ["timer_service_overrunning %s (%s %d s, bound %d s)" % (unit, expected, int(age), bound)], None, st
        return problems, None, st                             # a start in the future: not this clock, not believed
    again = timer_state(unit, run=run)
    if next_elapse(again) is not None:
        return timer_problems(again, now=now, bound_seconds=bound_seconds), None, again
    return problems, None, st


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
    this_update = None
    try:
        rl = _revocation.read(crl, anchor, effective)
        facts["list_number"] = rl.number
        facts["_this_update"] = this_update = rl.this_update
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
    prev = _verified_previous(stand, view, tls)
    published_at = _published_at(stand, pair, facts)
    for tid in live:
        ack = acks.get(tid)
        if not ack:
            problems.append("ack_missing %s" % tid)
            continue
        wrong = trust_install.differences(ack, pair)
        if wrong and prev and pair and not trust_install.differences(ack, prev) \
                and prev.get("content_sha256") == pair.get("content_sha256"):
            # trust-floor R10: the admissible predecessor, inside the distribution window, is not red
            if published_at is not None and now - published_at <= DISTRIBUTION_WINDOW_SECONDS:
                facts.setdefault("ack_previous", []).append(tid)
            else:
                problems.append("distribution_overdue %s (on the predecessor %s, current published %s)"
                                % (tid, ack.get("revocation_list_number"),
                                   _iso(published_at) if published_at else "unknown"))
        elif wrong:
            problems.append("ack_mismatch %s (%s)" % (tid, ",".join(wrong)))
        try:
            float(ack["acked_at"])                            # a malformed ack; its AGE is no fault (D2)
        except (KeyError, TypeError, ValueError):
            problems.append("ack_time_missing %s" % tid)
    problems += _rotation_problems(stand, now=now, this_update=this_update, pair=pair, live=live, acks=acks,
                                   facts=facts)
    return problems, facts


def _rotation_problems(stand, *, now: float, this_update: float | None, pair: dict | None, live: list, acks: dict,
                       facts: dict) -> list[str]:
    """trust-watchdog D2: the age is judged on the PAIR. `rotation_overdue` when the verified current list's
    thisUpdate -- the age the cycle itself rotates by (`trust_publication.local`) -- is more than the cycle's own
    rotation age (`_rotate_after()`, never a second constant) + 900 s old, while no live target differs from the
    current pair (a lagging target is red on its own and holds the rotation back by design) and no publication
    record is open (that one explains the transition)."""
    from agentnode_sdk.remote_mcp import trust_publication as TP
    from agentnode_sdk.worker import trust_install
    if this_update is None or pair is None:
        return []
    if any(not acks.get(tid) or trust_install.differences(acks.get(tid), pair) for tid in live):
        return []
    try:
        if any(r["kind"] == TP.PUBLICATION and r["state"] == TP.OPEN for r in TP.load_records(stand)):
            return []
    except ValueError:
        return []                                             # named by publication_problems
    due = TP._rotate_after() + ROTATION_GRACE_SECONDS
    age = now - this_update
    facts["list_age_seconds"] = int(age)
    if age > due:
        return ["rotation_overdue (current #%s thisUpdate %s, %d s old, due after %d s)"
                % (facts.get("list_number"), _iso(this_update), int(age), int(due))]
    return []


DISTRIBUTION_WINDOW_SECONDS = 600
PUBLICATION_HUNG_SECONDS = 600


def _verified_previous(stand, view, tls) -> dict | None:
    """The saved predecessor's facts, only if this control's anchor verifies both documents (as the router)."""
    from agentnode_sdk.pki import revocation as _revocation
    from agentnode_sdk.pki import tombstones as _tombstones
    from agentnode_sdk.remote_mcp import targets as T
    from agentnode_sdk.worker import trust_install
    pair = T.previous_pair(stand)
    if pair is None:
        return None
    try:
        effective = view.effective_time()
        _revocation.read(pair[0], view.anchor(), effective)
        _tombstones.read(pair[1], deployment=tls.deployment, verifier=view._verify_with_anchor).usable_at(effective)
        return trust_install.facts_of(*pair)
    except Exception:                                         # noqa: BLE001 - not believed
        return None


def _published_at(stand, pair, facts) -> float | None:
    """When the current pair was published: the record that published it, else the list's thisUpdate."""
    from agentnode_sdk.remote_mcp import trust_publication as TP
    from agentnode_sdk.worker import trust_install
    try:
        for r in reversed(TP.load_records(stand)):
            if r["kind"] == TP.PUBLICATION and r.get("result") and pair \
                    and not trust_install.differences(r["result"], pair):
                return float(r.get("published_at") or r["created_at"])
    except ValueError:
        pass                                                  # named by publication_problems
    return facts.pop("_this_update", None)


def publication_problems(stand, *, now: float, run: Runner = _run, cycle_timer: str = CYCLE_TIMER,
                         floor_timer: str = FLOOR_TIMER, live: list | None = None,
                         monotonic: Callable[[], float] = time.monotonic,
                         boot_id: Callable[[], str | None] | None = None) -> tuple[list[str], dict, list[str]]:
    """trust-floor C20/C21: both new timers finite, the journal readable with no record open > 10 min (lock
    held or not, read from /proc/locks), the predecessor pointer readable, consistent and naming a generation
    whose bytes match it and whose facts equal what its record saved, and the last cycle successful recently;
    trust-watchdog D3-D5: every live target reached recently, and a missing first cycle `pending` while the
    cycle timer's activation is initializing. Returns (problems, facts, pending)."""
    from agentnode_sdk.remote_mcp import target_ops as O
    from agentnode_sdk.remote_mcp import targets as T
    from agentnode_sdk.remote_mcp import trust_publication as TP
    problems: list[str] = []
    pending: list[str] = []
    facts: dict[str, Any] = {}
    states = {}
    for unit, role in ((cycle_timer, "cycle"), (floor_timer, "floor")):
        found, note, st = judge_timer(unit, role=role, now=now, bound_seconds=FIVE_MINUTE_BOUND_SECONDS, run=run,
                                      monotonic=monotonic)
        problems += found
        if note:
            facts.setdefault("timer_running", []).append(note)
        states[unit] = st
        nxt = next_elapse(st)
        facts[unit + "_next"] = _iso(nxt) if nxt else None
    held = held_flock_inodes()
    tdir = Path(stand) / "targets"
    try:
        records = TP.load_records(stand)
    except ValueError as exc:
        records = []
        problems.append("trust_journal_unreadable (%s)" % exc)
    for r in records:
        if r["state"] != TP.OPEN:
            continue
        age = now - float(r.get("created_at") or 0)
        if age > PUBLICATION_HUNG_SECONDS:
            lock = Path(str(tdir / (O.REFRESH_LOCK_NAME if r.get("phase") in ("begun", "previous_saved")
                                    else TP.DISTRIBUTION_LOCK_NAME)) + ".lock")
            problems.append("trust_publication_hung %s %s %s (%d s open, %s)" % (
                r["record_id"], r["kind"], r.get("phase"), int(age), _lock_held(lock, held)))
    try:
        pointer = TP.load_pointer(stand)
    except ValueError as exc:
        pointer = None
        problems.append("previous_pointer_unreadable (%s)" % exc)
    else:
        if pointer is None:
            if any(r["kind"] == TP.PUBLICATION and r.get("previous") not in (None, "unverifiable") for r in records):
                problems.append("previous_missing (a record saved a predecessor, no pointer selects it)")
        else:
            pair = T.previous_pair(stand)
            saver = next((r for r in records if r["record_id"] == pointer.get("record_id")), None)
            if pair is None:
                problems.append("previous_missing (pointer %s names no consistent generation)" % pointer.get("generation"))
            elif saver is None or not saver.get("from") or \
                    saver["from"].get("revocation_list_sha256") != pointer.get("revocation_list_sha256") or \
                    saver["from"].get("tombstones_sha256") != pointer.get("tombstones_sha256"):
                problems.append("previous_mismatch (generation %s is not what record %s saved)"
                                % (pointer.get("generation"), pointer.get("record_id")))
            facts["previous"] = {"generation": pointer.get("generation"), "number": pointer.get("revocation_list_number")}
    try:
        state = TP.load_cycle_state(stand)
    except ValueError as exc:
        state = None
        problems.append("trust_cycle_state_unreadable (%s)" % exc)
    boot = (boot_id or TP.current_boot_id)()
    mono = monotonic()
    init = initialization(cycle_timer, run=run, monotonic=monotonic, timer=states.get(cycle_timer))
    facts["cycle_initialization"] = init["status"]
    since = float((state or {}).get("last_success_at") or 0)
    age = _evidence_age((state or {}).get("last_success_mono"), (state or {}).get("boot_id"), since or None,
                        now=now, mono=mono, boot=boot)
    if age.get("regressed") is not None:
        problems.append("clock_regressed (cycle success, %d s)" % age["regressed"])
    elif age["age"] is None or age["age"] > CYCLE_SUCCESS_MAX_AGE_SECONDS:
        last = (state or {}).get("last_run") or {}
        what = "trust_cycle_not_succeeding (last success %s, last run %s %s" % (
            _iso(since) if since else "never", last.get("status"), str(last.get("failure", ""))[:80])
        if init["status"] == "initializing" and not age.get("same_boot_stale"):
            pending.append(what + ", initializing %d s since the cycle timer's activation)" % init["age"])
        elif init["status"] == "expired":
            problems.append(what + ", initializing expired after %d s)" % init["age"])
        elif init["status"] == "failed":
            problems.append(what + ", the first cycle since activation finished %s)" % init["detail"])
        else:
            problems.append(what + ")")
    facts["cycle_last_success"] = _iso(since) if since else None
    more, rpending, rfacts = reach_problems((state or {}).get("reach") or {}, live=live or [], now=now, mono=mono,
                                            boot=boot, initializing=init["status"] == "initializing")
    problems += more
    pending += rpending
    facts.update(rfacts)
    return problems, facts, pending


def initialization(cycle_timer: str = CYCLE_TIMER, *, run: Runner = _run,
                   monotonic: Callable[[], float] = time.monotonic, timer: dict | None = None) -> dict:
    """trust-watchdog D4: where the cycle timer's CURRENT activation stands, on systemd's monotonic facts only.
    `initializing` while no run of its service has finished since the timer became active (an install, a forward
    switch, a restart, a boot) and that activation is at most INIT_GRACE_SECONDS old; `expired` after that with no
    run finished; `failed` when the first run that finished since then did not succeed; `completed` when it did;
    `unknown` when systemd does not say (then nothing is pending: the plain judgement applies)."""
    st = timer if timer is not None and "ActiveEnterTimestampMonotonic" in timer else timer_state(cycle_timer, run=run)
    act = _usec(st.get("ActiveEnterTimestampMonotonic"))
    if act is None:
        return {"status": "unknown", "age": 0, "detail": ""}
    age = monotonic() - act / 1_000_000.0
    if age < 0:
        return {"status": "unknown", "age": 0, "detail": "activation in the future"}
    svc = service_state(cycle_timer[:-len(".timer")] + ".service" if cycle_timer.endswith(".timer") else cycle_timer,
                        run=run)
    ended = _usec(svc.get("ExecMainExitTimestampMonotonic"))
    if ended is not None and ended >= act:
        if svc.get("Result") == "success" and str(svc.get("ExecMainStatus")) == "0":
            return {"status": "completed", "age": int(age), "detail": ""}
        return {"status": "failed", "age": int(age),
                "detail": "result %s status %s" % (svc.get("Result"), svc.get("ExecMainStatus"))}
    if age <= INIT_GRACE_SECONDS:
        return {"status": "initializing", "age": int(age), "detail": ""}
    return {"status": "expired", "age": int(age), "detail": ""}


def _evidence_age(at_mono, at_boot, at_wall, *, now: float, mono: float, boot: str | None) -> dict:
    """trust-watchdog D5/R3: how old a piece of the cycle's evidence is. Monotonic when it was taken in THIS boot
    (a wall-clock jump cannot age or refresh it); else the wall clock, where a clock more than 300 s behind the
    evidence is `regressed` (named red) and a smaller difference counts as 0. `age` None = no evidence."""
    if at_mono is not None and boot is not None and at_boot == boot:
        try:
            age = mono - float(at_mono)
        except (TypeError, ValueError):
            age = None
        if age is not None and age >= 0:
            return {"age": age, "same_boot_stale": age > CYCLE_SUCCESS_MAX_AGE_SECONDS}
    if not at_wall:
        return {"age": None}
    age = now - float(at_wall)
    if age < -CLOCK_REGRESSION_SECONDS:
        return {"age": None, "regressed": int(age)}
    return {"age": max(age, 0.0)}


def reach_problems(reach: dict, *, live: list, now: float, mono: float, boot: str | None,
                   initializing: bool) -> tuple[list[str], list[str], dict]:
    """trust-watchdog D3: every live target reached by the cycle (an exact push or the router's handshake-only
    preflight) within REACH_MAX_AGE_SECONDS, judged monotonically in this boot. Older: `target_unreachable` with
    the router's classification, the first failure of the episode and the last success. No evidence in this boot:
    `target_reach_unknown` -- `pending` while the cycle timer is initializing (D4), red afterwards."""
    problems: list[str] = []
    pending: list[str] = []
    facts: dict[str, Any] = {"reach": {}}
    for tid in sorted(live):
        e = reach.get(tid) if isinstance(reach.get(tid), dict) else None
        if e is None:
            (pending if initializing else problems).append("target_reach_unknown %s (no reach recorded)" % tid)
            continue
        age = _evidence_age(e.get("ok_mono"), e.get("boot_id"), e.get("ok_at"), now=now, mono=mono, boot=boot)
        same_boot = e.get("boot_id") == boot and boot is not None
        facts["reach"][tid] = {"last_reached": _iso(float(e["ok_at"])) if e.get("ok_at") else None,
                               "code": e.get("code") or ""}
        if age.get("regressed") is not None:
            problems.append("clock_regressed (reach %s, %d s)" % (tid, age["regressed"]))
        elif age["age"] is not None and age["age"] <= REACH_MAX_AGE_SECONDS:
            continue
        elif not same_boot and initializing:
            pending.append("target_reach_unknown %s (no reach in this boot yet)" % tid)
        elif e.get("code") or e.get("failing_since"):
            problems.append("target_unreachable %s (%s, failing since %s, last reached %s)" % (
                tid, e.get("code") or "unknown", _iso(float(e["failing_since"])) if e.get("failing_since") else "unknown",
                _iso(float(e["ok_at"])) if e.get("ok_at") else "never"))
        else:
            problems.append("target_unreachable %s (not reached for %s, last reached %s)" % (
                tid, "%d s" % age["age"] if age["age"] is not None else "unknown",
                _iso(float(e["ok_at"])) if e.get("ok_at") else "never"))
    return problems, pending, facts


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
                         renew_timer: str = RENEW_TIMER,
                         monotonic: Callable[[], float] = time.monotonic) -> tuple[list[str], dict]:
    """Every active target's record valid with more than its class warning left (effective, pinned time);
    the default target active; the renewal timer finite; the renewal state present, recent and agreeing
    with the register; no target degraded or blocked; no renew/probe operation open for 30 minutes."""
    from agentnode_sdk.remote_mcp import targets as T
    from agentnode_sdk.remote_mcp import target_ops as O

    problems: list[str] = []
    facts: dict[str, Any] = {}
    found, note, tstate = judge_timer(renew_timer, role="renew", now=now, bound_seconds=RENEW_BOUND_SECONDS, run=run,
                                      monotonic=monotonic)
    problems += found
    if note:
        facts["timer_running"] = [note]
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


def _previous_report(state_file) -> dict | None:
    """The last run's report, or None = previous unknown (missing, unreadable, malformed). Never a reason to be
    green: an unknown previous state only means no recovery can be recorded (D6)."""
    try:
        doc = json.loads(Path(state_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("state") in ("green", "red", "initializing") else None


def _red_since(prev: dict | None) -> str | None:
    """When the red episode the previous run was in began. A red report of the parent release (no `red_since`)
    counts as red since its own check (R4); an `initializing` run carries an episode on without ending it."""
    if not prev:
        return None
    if prev.get("state") == "red":
        return prev.get("red_since") or prev.get("checked_at")
    if prev.get("state") == "initializing":
        return prev.get("red_since")
    return None


def _codes(problems) -> str:
    return ",".join(sorted({str(p).split()[0] for p in problems})) or "-"


def _append_recovery(log: Path, red_since: str, line: str) -> str:
    """D6/R4: ONE O_APPEND write of the recovery line, unless the log already holds the line of this episode (a
    run that crashed after the write and before its marker). Never truncates, rewrites or rotates the log."""
    try:
        if ("recovered (red since %s:" % red_since) in log.read_text(encoding="utf-8", errors="replace"):
            return "already logged"
    except FileNotFoundError:
        pass
    data = (line + "\n").encode("utf-8")
    fd = os.open(str(log), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    try:
        if os.write(fd, data) != len(data):
            raise OSError("short write")
        os.fsync(fd)
    finally:
        os.close(fd)
    return "appended"


def check(stand, *, now: float | None = None, timer: str = REFRESH_TIMER, state_file=STATE_FILE,
          run: Runner = _run, tls=None, view=None, renew_state_file=None, renew_timer: str = RENEW_TIMER,
          cycle_timer: str = CYCLE_TIMER, floor_timer: str = FLOOR_TIMER, alerts_log=None,
          monotonic: Callable[[], float] = time.monotonic, boot_id: Callable[[], str | None] | None = None) -> dict:
    """One watchdog run. Returns the report that was written; `report["state"]` is green, initializing (only
    `pending` items, no problem: D4) or red. A red episode is remembered (`red_since`, `last_red`); the first green
    run after it records `recovered_at`/`recovered_from` and appends one recovery line to the alert log (D6)."""
    now = time.time() if now is None else float(now)
    try:
        problems, facts = trust_problems(stand, now=now, tls=tls, view=view)
    except Exception as exc:                                  # noqa: BLE001 - an unknown failure is red
        problems, facts = ["watchdog_error (%s)" % type(exc).__name__], {}
    pending: list[str] = []
    found, note, tstate = judge_timer(timer, role=ROLES.get(timer, "refresh"), now=now,
                                      bound_seconds=REFRESH_BOUND_SECONDS, run=run, monotonic=monotonic)
    problems += found
    if note:
        facts.setdefault("timer_running", []).append(note)
    nxt = next_elapse(tstate)
    facts["refresh_next"] = _iso(nxt) if nxt else None
    try:
        more, afacts = attestation_problems(stand, now=now, run=run, state_path=renew_state_file,
                                            renew_timer=renew_timer, monotonic=monotonic)
    except Exception as exc:                                  # noqa: BLE001 - an unknown failure is red
        more, afacts = ["attestation_watchdog_error (%s)" % type(exc).__name__], {}
    problems += more
    running = facts.pop("timer_running", []) + afacts.pop("timer_running", [])
    facts.update(afacts)
    try:
        more, pfacts, ppending = publication_problems(stand, now=now, run=run, cycle_timer=cycle_timer,
                                                      floor_timer=floor_timer, live=facts.get("live_targets"),
                                                      monotonic=monotonic, boot_id=boot_id)
    except Exception as exc:                                  # noqa: BLE001 - an unknown failure is red
        more, pfacts, ppending = ["publication_watchdog_error (%s)" % type(exc).__name__], {}, []
    problems += more
    pending += ppending
    running += pfacts.pop("timer_running", [])
    facts.update(pfacts)
    if running:
        facts["timer_running"] = running
    facts.pop("_this_update", None)
    checked_at = _iso(now)
    prev = _previous_report(state_file)
    red_since = _red_since(prev)
    report = {"state": "red" if problems else ("initializing" if pending else "green"), "checked_at": checked_at,
              "problems": problems, "pending": pending, "facts": facts}
    for key in ("last_red", "recovered_at", "recovered_from", "recovery_logged"):
        if prev and key in prev and red_since is None:
            report[key] = prev[key]                           # the last episode's record rolls on while green
    if prev and "last_red" in prev and red_since is not None:
        report["last_red"] = prev["last_red"]
    log = Path(alerts_log) if alerts_log is not None else Path(state_file).parent / ALERTS_LOG_NAME
    if report["state"] == "red":
        report["red_since"] = red_since or checked_at
        report["last_red"] = {"checked_at": checked_at, "problems": list(problems)}
        for key in ("recovered_at", "recovered_from", "recovery_logged"):
            report.pop(key, None)
    elif report["state"] == "initializing":
        if red_since is not None:
            report["red_since"] = red_since                   # initializing never ends an episode
    elif red_since is not None:
        codes = _codes((report.get("last_red") or {}).get("problems") or (prev or {}).get("problems") or [])
        report["recovered_at"] = checked_at
        report["recovered_from"] = {"red_since": red_since, "codes": codes}
        report["recovery_logged"] = False
    return _write_and_log(report, state_file, log)


def _write_and_log(report: dict, state_file, log: Path) -> dict:
    """The state first, durably; then the recovery line (if one is owed); then the marker that it was written."""
    try:
        write_state(state_file, report)
    except OSError as exc:
        report["problems"].append("state_file_unwritable (%s)" % type(exc).__name__)
        report["state"] = "red"
        return report
    if report["state"] == "green" and report.get("recovered_from") and report.get("recovery_logged") is False:
        rf = report["recovered_from"]
        line = "%s rmcp-trust-watchdog recovered (red since %s: %s)" % (report["recovered_at"], rf["red_since"],
                                                                         rf["codes"])
        try:
            _append_recovery(log, rf["red_since"], line)
        except OSError as exc:
            # not recovered after all: the episode goes on, and the next green run owes the line again
            report["problems"].append("alerts_log_unwritable (%s)" % type(exc).__name__)
            report["state"] = "red"
            report["red_since"] = rf["red_since"]             # `last_red` keeps the episode's own problems
            for key in ("recovered_at", "recovered_from", "recovery_logged"):
                report.pop(key, None)
            try:
                write_state(state_file, report)
            except OSError as exc2:
                report["problems"].append("state_file_unwritable (%s)" % type(exc2).__name__)
            return report
        report["recovery_logged"] = True
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
    c.add_argument("--cycle-timer", default=CYCLE_TIMER)
    c.add_argument("--floor-timer", default=FLOOR_TIMER)
    t = sub.add_parser("timer-check")
    t.add_argument("--unit", action="append", required=True)
    t.add_argument("--bound-seconds", type=float, action="append", required=True)
    t.add_argument("--role", action="append", choices=sorted(RUN_BOUND_SECONDS), default=None,
                   help="the unit's role (its run bound, D1); default: the role of a shipped timer's name")
    args = p.parse_args(argv)
    if args.cmd == "check":
        report = check(args.stand, timer=args.timer, state_file=args.state_file, renew_timer=args.renew_timer,
                       renew_state_file=args.renew_state_file, cycle_timer=args.cycle_timer,
                       floor_timer=args.floor_timer)
        print(json.dumps(report, indent=1, sort_keys=True))
        return 0 if report["state"] in ("green", "initializing") else 1
    if len(args.unit) != len(args.bound_seconds):
        p.error("one --bound-seconds per --unit")
    roles = args.role or [ROLES.get(u) for u in args.unit]
    if len(roles) != len(args.unit) or None in roles:
        p.error("one --role per --unit for a timer that is not one of the shipped ones")
    now = time.time()
    problems = []
    for unit, bound, role in zip(args.unit, args.bound_seconds, roles):
        found, note, st = judge_timer(unit, role=role, now=now, bound_seconds=bound)
        nxt = next_elapse(st)
        print("%s %s next=%s%s" % ("RED" if found else "OK", unit, _iso(nxt) if nxt else "none",
                                   (" " + note) if note else ""))
        problems += found
    for line in problems:
        print("ALERT %s" % line)
    return 1 if problems else 0


__all__ = ["ALERTS_LOG_NAME", "INIT_GRACE_SECONDS", "MARGIN_SECONDS", "REACH_MAX_AGE_SECONDS", "REFRESH_BOUND_SECONDS",
           "REFRESH_TIMER", "RENEW_BOUND_SECONDS", "RENEW_RUN_MAX_AGE_SECONDS", "RENEW_TIMER", "ROLES",
           "ROTATION_GRACE_SECONDS", "RUN_BOUND_SECONDS", "STATE_FILE", "WATCHDOG_BOUND_SECONDS", "WATCHDOG_TIMER",
           "attestation_problems", "check", "held_flock_inodes", "initialization", "judge_timer", "main",
           "next_elapse", "reach_problems", "service_state", "timer_problems", "timer_state", "trust_problems",
           "write_state"]


if __name__ == "__main__":
    sys.exit(main())
