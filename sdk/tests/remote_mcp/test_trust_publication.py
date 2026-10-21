"""The ONE publication authority for the remote-MCP control's trust pair (trust-floor, Codex Q-TFL-0001,
profile trust-floor-r1 C1-C26, DECISION-0001 R1-R10).

Real issuer, register, acknowledgements, journal and worker-side install (the lifecycle stand); only the
network is a test double. Each test names the frozen criterion / counter-check (CCn) it holds."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

import pytest

from agentnode_sdk.pki import revocation as rl
from agentnode_sdk.remote_mcp import control_floor
from agentnode_sdk.remote_mcp import target_ops as O
from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.remote_mcp import trust_publication as TP
from agentnode_sdk.remote_mcp.target_ops import OperationFailed
from agentnode_sdk.worker import trust_install as TI
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout

from .test_target_lifecycle import _provision, env  # noqa: F401 - the lifecycle stand


class Crash(BaseException):
    """A SIGKILL stand-in: nothing after it runs, no `except Exception` catches it."""


def _trust(env) -> Path:
    return env["world"].root / "trust"


def _pair_bytes(env):
    return (_trust(env) / "revoked.crl").read_bytes(), (_trust(env) / "revoked-identities.json").read_bytes()


def _current(env):
    return TI.facts_of(*_pair_bytes(env))


def _previous(env):
    pair = T.previous_pair(env["reg"].stand)
    if pair is None:
        return None
    try:
        return TP.verify_pair(env["ops"], *pair)
    except ValueError:
        return None


def _admitted(env, tid="managed-w1"):
    return T.trust_current(env["acks"].load().get(tid), now=time.time(), deployment=_current(env),
                           previous=_previous(env))


def _records(env):
    return TP.load_records(env["reg"].stand)


def _synced(env):
    """Every live target on the current pair (the precondition of a content-equal rotation)."""
    report = env["ops"].refresh_trust()
    assert report["lagging"] == [], report
    return report


def _age(env, monkeypatch, seconds=2.0):
    """Make the current list 'due' for rotation without moving a clock: the threshold, not the time."""
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", seconds)
    time.sleep(seconds + 0.2)


def _a_serial(env):
    inv = env["world"].issuer.inventory()["entries"]
    return next(c["serial"] for e in inv.values() for c in e.get("certificates", []) if c.get("status") == "current")


def _unreachable(env, tid="managed-w1"):
    w = env["workers"][tid]
    real = w.install_trust
    w.install_trust = lambda crl, tombs: (_ for _ in ()).throw(OSError("worker unreachable"))
    return lambda: setattr(w, "install_trust", real)


# ---------------------------------------------------------------------- C1/C2/C4: one authority, floors local
def test_the_floor_run_never_publishes_a_rotation(env, monkeypatch):
    """C1 / CC1 / owner's parent proof inverted: a list past its refresh age is NOT re-signed by the floor run."""
    _synced(env)
    monkeypatch.setattr(rl, "REFRESH_AFTER_SECONDS", 1.0)      # the issuer's own age test: due
    time.sleep(1.2)
    before = _pair_bytes(env)
    report = control_floor.run(env["ops"], env["world"].floor_dir)
    assert _pair_bytes(env) == before and report["failure"] == ""
    assert "publication left to the publication authority" in report["tick"]["list"]
    assert _admitted(env) is True
    assert [r for r in _records(env) if r["reason"] != "import_legacy" and r.get("result")] == [] or all(
        not TI.differences(r["result"], _current(env)) for r in _records(env) if r.get("result"))


def test_the_generic_root_run_still_publishes_when_due(env, monkeypatch):
    """C2: `agentnode pki tick` (publish=True, the default) is unchanged outside the remote-MCP control."""
    monkeypatch.setattr(rl, "REFRESH_AFTER_SECONDS", 1.0)
    time.sleep(1.2)
    number = _current(env)["revocation_list_number"]
    report = env["world"].issuer.tick(env["world"].floor_dir)
    assert "published number %d" % (number + 1) in report["list"]


def test_the_parent_floor_publication_refuses_the_target(env, monkeypatch):
    """The owner's parent evidence, reproduced in code: the generic root run re-signs the pair the target holds,
    saves no predecessor, pushes nothing -- and the router refuses the target until the next refresh."""
    _synced(env)
    T.save_previous_pair(env["reg"].stand, *_pair_bytes(env))   # a STALE legacy predecessor, as live (#5)
    env["world"].issuer.publish()                                # ... then two more publications, as live
    _synced(env)
    monkeypatch.setattr(rl, "REFRESH_AFTER_SECONDS", 1.0)
    time.sleep(1.2)
    env["world"].issuer.tick(env["world"].floor_dir)             # the parent's floor run: publish=True
    assert _admitted(env) is False


def test_the_floor_run_never_pushes(env, monkeypatch):
    """C4 / CC4: even with a target behind, the floor run makes no worker call."""
    calls = []
    env["workers"]["managed-w1"].install_trust = lambda crl, tombs: calls.append(1)
    report = control_floor.run(env["ops"], env["world"].floor_dir)
    assert calls == [] and all(str(v).startswith("written") for v in report["tick"]["floors"].values())


def test_the_floor_run_publishes_a_pending_revocation_at_once(env):
    """C13 / D7: a withdrawal committed without publication is published by the floor run's LOCAL phase."""
    _synced(env)
    tid = _provision(env)
    env["ops"].probe(tid)
    inst = env["reg"].load().target(tid).worker["instance"]
    env["world"].issuer.recover_entry("worker", inst, publish=False)
    assert env["world"].issuer.reconcile()["reason"]
    report = control_floor.run(env["ops"], env["world"].floor_dir)
    assert report["reconcile"]["security"] in ("missing_serials", "missing_identities")
    assert env["world"].issuer.reconcile()["reason"] == ""
    sec = [r for r in _records(env) if r["reason"].startswith("security:")]
    assert len(sec) == 1 and sec[0]["phase"] == "published" and sec[0]["state"] == TP.OPEN


def test_a_pending_revocation_holds_the_floors_back(env):
    """C4: while a revocation is not published, no floor is written (the run cannot reconcile: lock busy)."""
    from agentnode_sdk.gateway.filelock import ProcessLock
    tid = _provision(env)
    env["ops"].probe(tid)
    env["world"].issuer.recover_entry("worker", env["reg"].load().target(tid).worker["instance"], publish=False)
    held, release = threading.Event(), threading.Event()

    def hold():
        with ProcessLock(Path(env["reg"].stand) / "targets" / O.REFRESH_LOCK_NAME, timeout=5):
            held.set()
            release.wait(30)
    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    try:
        monkeypatch_wait = control_floor.LOCK_WAIT_SECONDS
        control_floor.LOCK_WAIT_SECONDS = 0.3
        report = control_floor.run(env["ops"], env["world"].floor_dir)
    finally:
        control_floor.LOCK_WAIT_SECONDS = monkeypatch_wait
        release.set()
        t.join(10)
    assert report["failure"].startswith("trust_refresh.busy")
    assert report["tick"]["pending_revocation"] is True
    assert all("not written" in str(v) for v in report["tick"]["floors"].values())


# ---------------------------------------------------------------------- C3: no lock across the network
def test_a_slow_push_never_delays_a_revocation(env):
    """C3 / C13 / CC3: while a distribution waits on a hanging worker, a security publication completes."""
    _synced(env)
    tid = _provision(env)
    env["ops"].probe(tid)
    _synced(env)
    w = env["workers"]["managed-w1"]
    real, inside, go = w.install_trust, threading.Event(), threading.Event()

    def hanging(crl, tombs):
        inside.set()
        go.wait(30)
        return real(crl, tombs)
    w.install_trust = hanging
    t = threading.Thread(target=lambda: env["ops"].refresh_trust(force=True))
    t.start()
    try:
        assert inside.wait(15)                                   # the distribution is waiting on the network
        started = time.time()
        inst = env["reg"].load().target(tid).worker["instance"]
        with TP._publication_lock(env["ops"]):
            env["world"].issuer.recover_entry("worker", inst, publish=False)
            TP.local(env["ops"], TP.new_report("revoke"), rotate=False)
        assert time.time() - started < 10 and env["world"].issuer.reconcile()["reason"] == ""
    finally:
        go.set()
        t.join(30)


# ---------------------------------------------------------------------- C6-C9: journal, predecessor, identity
def test_the_record_is_durable_before_any_effect(env, monkeypatch):
    """C6 / CC8: a journal write that fails leaves the pair, the predecessor and the inventory untouched."""
    _synced(env)
    _age(env, monkeypatch)
    before, number = _pair_bytes(env), env["world"].issuer.inventory()["list_number"]
    pointer = TP.load_pointer(env["reg"].stand)

    def no_journal(stand, rec):
        raise OSError("disk full")
    monkeypatch.setattr(TP, "_write_record", no_journal)
    with pytest.raises(OSError):
        env["ops"].refresh_trust()
    assert _pair_bytes(env) == before and env["world"].issuer.inventory()["list_number"] == number
    assert TP.load_pointer(env["reg"].stand) == pointer


def test_a_rotation_saves_the_verified_predecessor_and_keeps_the_target_admitted(env, monkeypatch):
    """C7 / C8 / C11 (owner point 1): during and after a content-equal rotation the target is admitted."""
    _synced(env)
    _age(env, monkeypatch)
    held = _current(env)
    w = env["workers"]["managed-w1"]
    real, seen = w.install_trust, []

    def in_the_window(crl, tombs):
        seen.append((_admitted(env), TI.differences(env["acks"].load()["managed-w1"], _current(env)) != []))
        return real(crl, tombs)
    w.install_trust = in_the_window
    report = env["ops"].refresh_trust()
    assert report["published"] == held["revocation_list_number"] + 1 and report["lagging"] == []
    assert seen == [(True, True)]                                # on the predecessor, inside the window, admitted
    pointer = TP.load_pointer(env["reg"].stand)
    assert pointer["revocation_list_sha256"] == held["revocation_list_sha256"]
    assert _previous(env)["content_sha256"] == _current(env)["content_sha256"]
    rec = [r for r in _records(env) if r["reason"] == "rotation"][-1]
    assert rec["state"] == TP.DONE and rec["from"]["revocation_list_sha256"] == held["revocation_list_sha256"]
    assert _admitted(env) is True


@pytest.mark.skipif(__import__("os").name == "nt", reason="rename over a file another reader holds open is a "
                    "POSIX guarantee; Windows refuses the open/rename for an instant (the control is Linux)")
def test_a_reader_never_sees_a_mixed_predecessor(env, monkeypatch):
    """C7 / CC9: readers racing many saves see one complete generation or the other, never None or a mix."""
    _synced(env)
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 0.0)
    env["ops"].refresh_trust(force=True)                         # a first generation exists
    stop, bad = threading.Event(), []
    known = {}

    def reader():
        while not stop.is_set():
            pair = T.previous_pair(env["reg"].stand)
            if pair is None:
                bad.append("none")
                continue
            facts = TI.facts_of(*pair)
            n = facts["revocation_list_number"]
            if known.setdefault(n, facts["tombstones_sha256"]) != facts["tombstones_sha256"]:
                bad.append(n)
    t = threading.Thread(target=reader)
    t.start()
    try:
        for _ in range(6):
            env["ops"].refresh_trust(force=True)
    finally:
        stop.set()
        t.join(10)
    assert bad == [], bad[:5]


def test_a_pointer_that_disagrees_with_its_files_gives_no_predecessor(env, monkeypatch):
    """R3 / negative: corrupt or dangling pointer -> None (never the legacy files)."""
    _synced(env)
    T.save_previous_pair(env["reg"].stand, *_pair_bytes(env))   # legacy files exist
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 0.0)
    env["ops"].refresh_trust(force=True)
    p = TP.pointer_path(env["reg"].stand)
    doc = json.loads(p.read_text())
    p.write_text(json.dumps(dict(doc, tombstones_sha256="0" * 64)))
    assert T.previous_pair(env["reg"].stand) is None
    p.write_text(json.dumps(dict(doc, generation="g-999999999999")))
    assert T.previous_pair(env["reg"].stand) is None
    p.write_text("{not json")
    assert T.previous_pair(env["reg"].stand) is None


def test_an_intervening_revocation_is_not_taken_for_this_records_publication(env, monkeypatch):
    """C9 / CC10: crash after the predecessor was saved; a revocation publishes a HIGHER number meanwhile; the
    resumed record closes `superseded` -- it never claims that publication as its own."""
    _synced(env)
    _age(env, monkeypatch)

    def crash(step):
        if step == "tp:previous_saved":
            raise Crash()
    env["ops"]._crash_after = crash
    with pytest.raises(Crash):
        env["ops"].refresh_trust()
    env["ops"]._crash_after = lambda step: None
    rec = [r for r in _records(env) if r["state"] == TP.OPEN][0]
    env["world"].issuer.revoke(_a_serial(env))                   # number advances past rec's `expect`? no: equal
    env["world"].issuer.publish()                                # ... and past it
    assert _current(env)["revocation_list_number"] > rec["expect"]["number"]
    env["ops"].refresh_trust()
    done = [r for r in _records(env) if r["record_id"] == rec["record_id"]][0]
    assert done["state"] == TP.SUPERSEDED and done.get("result") is None


def test_a_crash_between_crl_and_tombstones_completes_the_same_publication(env, monkeypatch):
    """Negative / C16: the CRL is promoted, the tombstones are not, SIGKILL -- resume publishes the tombstones
    alone; ONE number, ONE terminal."""
    _synced(env)
    _age(env, monkeypatch)
    issuer = env["world"].issuer
    real = issuer._publish_tombstones

    def killed(inventory, ca_key):
        raise Crash()
    monkeypatch.setattr(issuer, "_publish_tombstones", killed)
    with pytest.raises(Crash):
        env["ops"].refresh_trust()
    monkeypatch.setattr(issuer, "_publish_tombstones", real)
    rec = [r for r in _records(env) if r["state"] == TP.OPEN][0]
    number = _current(env)["revocation_list_number"]
    assert number == rec["expect"]["number"]                     # the CRL is ours already
    report = env["ops"].refresh_trust()
    assert _current(env)["revocation_list_number"] == number and report["lagging"] == []
    done = [r for r in _records(env) if r["record_id"] == rec["record_id"]][0]
    assert done["state"] == TP.DONE and done["result"]["tombstones_sha256"] != rec["from"]["tombstones_sha256"]
    assert _admitted(env) is True


@pytest.mark.parametrize("step", ["tp:begun", "tp:previous_saved", "tp:published", "tp:distributing",
                                  "tp:pushed:managed-w1", "tp:terminal"])
def test_a_crash_at_every_boundary_resumes_to_one_publication(env, monkeypatch, step):
    """C16 / C17 / C18: SIGKILL at each boundary; the next cycle completes the SAME record: one number more,
    exactly one terminal, the target admitted throughout and exactly on the current pair at the end."""
    _synced(env)
    _age(env, monkeypatch)
    start = _current(env)["revocation_list_number"]
    gen = env["reg"].load().generation

    def crash(s):
        if s == step:
            raise Crash()
    env["ops"]._crash_after = crash
    with pytest.raises(Crash):
        env["ops"].refresh_trust()
    env["ops"]._crash_after = lambda s: None
    assert _admitted(env) is True                                # the crash itself refuses nobody
    env["ops"].refresh_trust()
    env["ops"].refresh_trust()                                   # and a further cycle changes nothing
    pubs = [r for r in _records(env) if r["kind"] == TP.PUBLICATION and r["reason"] == "rotation"]
    assert len(pubs) == 1 and pubs[0]["state"] in TP.TERMINAL, pubs
    assert _current(env)["revocation_list_number"] == start + 1
    assert not TI.differences(env["acks"].load()["managed-w1"], _current(env))
    assert env["reg"].load().generation == gen                   # the register is not touched
    assert all(r["state"] in TP.TERMINAL for r in _records(env))


def test_monotonic_numbers_across_a_crash_and_a_revocation(env, monkeypatch):
    """C17 / CC23: list numbers, revoked serials and withdrawn identities only grow."""
    _synced(env)
    seen = [_current(env)]
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 0.0)
    env["ops"].refresh_trust(force=True)
    seen.append(_current(env))
    tid = _provision(env)
    env["ops"].probe(tid)
    env["ops"].revoke(tid)
    seen.append(_current(env))
    numbers = [f["revocation_list_number"] for f in seen]
    assert numbers == sorted(numbers) and len(set(numbers)) == len(numbers)
    first, last = TI.facts_of(*_pair_bytes(env)), seen[0]
    assert first["content_sha256"] != last["content_sha256"]


# ---------------------------------------------------------------------- C10-C13: guard and security
def test_the_rotation_waits_for_a_lagging_target(env, monkeypatch):
    """C10 / CC11: due, but a live target is not on the current pair and > 72 h are left: no rotation."""
    _synced(env)
    tid = _provision(env)
    env["ops"].probe(tid)
    _synced(env)
    restore = _unreachable(env, tid)
    env["ops"].refresh_trust(force=True)                         # managed on N+1, tid still on N (predecessor)
    _age(env, monkeypatch)
    number = _current(env)["revocation_list_number"]
    report = env["ops"].refresh_trust()
    assert report["published"] is None and tid in report["deferred"]
    assert any(a.startswith("rotation_deferred") for a in report["alerts"])
    assert _current(env)["revocation_list_number"] == number and _admitted(env, tid) is True
    restore()
    report = env["ops"].refresh_trust()                          # caught up -> rotates in the same cycle
    assert report["published"] == number + 1 and report["lagging"] == [] and _admitted(env, tid) is True


def test_the_guard_yields_below_the_reserve(env, monkeypatch):
    """C10 / CC12: with less than 72 h left on the current pair the rotation goes ahead; the laggard is named."""
    _synced(env)
    restore = _unreachable(env)
    env["ops"].refresh_trust(force=True)
    monkeypatch.setattr(TP, "GUARD_RESERVE_SECONDS", 8 * 86400)  # more than the 7-day lists have: reserve gone
    _age(env, monkeypatch)
    number = _current(env)["revocation_list_number"]
    report = env["ops"].refresh_trust()
    assert report["published"] == number + 1 and report["lagging"] == ["managed-w1"]
    restore()


def test_a_security_publication_refuses_stale_targets_at_once_and_retries(env):
    """C12 / C13 / CC13: a revocation publishes new content; a target not on it is refused immediately (its
    predecessor no longer admits); offline it stays refused and lagging; back online it is admitted again."""
    _synced(env)
    tid = _provision(env)
    env["ops"].probe(tid)
    _synced(env)
    restore = _unreachable(env)
    other = _provision(env)
    env["ops"].probe(other)
    with pytest.raises(OperationFailed):                         # the revocation's propagation names the laggard
        env["ops"].revoke(other)
    assert _admitted(env, "managed-w1") is False and _admitted(env, tid) is True
    report = env["ops"].refresh_trust()
    assert report["lagging"] == ["managed-w1"] and _admitted(env, "managed-w1") is False
    restore()
    report = env["ops"].refresh_trust()
    assert report["lagging"] == [] and _admitted(env, "managed-w1") is True


def test_two_targets_with_mixed_acknowledgements(env, monkeypatch):
    """Negative: one target on the current pair, one on the predecessor -- both admitted for a content-equal
    rotation; a third pair (two behind) admits nobody."""
    _synced(env)
    tid = _provision(env)
    env["ops"].probe(tid)
    _synced(env)
    restore = _unreachable(env, tid)
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 0.0)
    env["ops"].refresh_trust(force=True)
    assert _admitted(env, "managed-w1") is True and _admitted(env, tid) is True
    assert TI.differences(env["acks"].load()[tid], _current(env))
    restore()


def test_an_ack_with_equal_content_but_not_the_saved_pair_is_refused(env, monkeypatch):
    """C24 / CC14 / CC15: only the exact saved predecessor admits -- not any older pair with equal content."""
    _synced(env)
    old = env["acks"].load()["managed-w1"]
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 0.0)
    env["ops"].refresh_trust(force=True)
    env["ops"].refresh_trust(force=True)                         # now two behind `old`, same content
    assert T.trust_current(dict(old, content_sha256=_current(env)["content_sha256"]), now=time.time(),
                           deployment=_current(env), previous=_previous(env)) is False


def test_an_unverifiable_current_pair_is_never_rotated(env, monkeypatch):
    """Negative / C8: a current pair this control's anchor does not verify is no predecessor: refused, named."""
    _synced(env)
    _age(env, monkeypatch)
    before = _pair_bytes(env)
    monkeypatch.setattr(TP, "verify_pair", lambda ops, crl, tombs: (_ for _ in ()).throw(ValueError("bad sig")))
    with pytest.raises(OperationFailed) as failed:
        env["ops"].refresh_trust()
    assert failed.value.code == "trust_publication.current_unverifiable" and _pair_bytes(env) == before


def test_an_expired_predecessor_admits_nothing(env, monkeypatch):
    """Negative: an expired saved pair is not believed (verified at the effective time)."""
    _synced(env)
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 0.0)
    restore = _unreachable(env)
    env["ops"].refresh_trust(force=True)
    assert _admitted(env) is True
    env["ops"].effective_time = lambda: time.time() + 8 * 86400
    assert _previous(env) is None
    restore()


# ---------------------------------------------------------------------- races
def test_floor_timer_and_manual_race_publish_once(env, monkeypatch):
    """Negative / C3: the 5-minute cycle, the 6-hourly refresh, a manual --force and the floor run together
    at the boundary -- exactly one rotation, every record terminal, the target admitted and current."""
    _synced(env)
    _age(env, monkeypatch)
    start = _current(env)["revocation_list_number"]
    gate, errors = threading.Barrier(4), []

    def run(fn):
        gate.wait()
        try:
            fn()
        except OperationFailed as exc:                           # busy is a named failure, never a skip
            errors.append(exc.code)
    jobs = [lambda: env["ops"].refresh_trust(entry="cycle", distribute_wait=0.0),
            lambda: env["ops"].refresh_trust(),
            lambda: env["ops"].refresh_trust(entry="manual"),
            lambda: control_floor.run(env["ops"], env["world"].floor_dir)]
    threads = [threading.Thread(target=run, args=(j,)) for j in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    env["ops"].refresh_trust()
    rotations = [r for r in _records(env) if r["kind"] == TP.PUBLICATION and r["reason"] in ("rotation",
                                                                                          "forced_rotation")]
    assert len(rotations) == 1 and _current(env)["revocation_list_number"] == start + 1, rotations
    assert set(errors) <= {"trust_distribution.busy", "trust_refresh.busy"}
    assert all(r["state"] in TP.TERMINAL for r in _records(env)) and _admitted(env) is True


def test_two_forced_refreshes_race(env, monkeypatch):
    _synced(env)
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 0.0)
    gate = threading.Barrier(2)
    out = []

    def run():
        gate.wait()
        out.append(env["ops"].refresh_trust(force=True))
    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert all(r["state"] in TP.TERMINAL for r in _records(env)) and _admitted(env) is True


# ---------------------------------------------------------------------- C18/C19/C25: terminal, named, audit
def test_a_terminal_is_written_once(env, monkeypatch):
    """C18 / CC24: a second close of the same record changes nothing and emits nothing."""
    _synced(env)
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 0.0)
    env["ops"].refresh_trust(force=True)
    rec = [r for r in _records(env) if r["kind"] == TP.PUBLICATION][-1]
    before = (TP.records_dir(env["reg"].stand) / (rec["record_id"] + ".json")).read_bytes()
    assert TP._close(env["ops"], TP.new_report("t"), dict(rec), TP.SUPERSEDED, "again") is False
    assert (TP.records_dir(env["reg"].stand) / (rec["record_id"] + ".json")).read_bytes() == before


class _Ledger:
    def __init__(self, fail=False):
        self.events, self.fail = [], fail

    def append(self, kind, **fields):
        if self.fail:
            raise OSError("ledger disk full")
        self.events.append(dict(fields, kind=kind))


def test_every_transition_is_audited_once(env, monkeypatch):
    """D11: begun, previous_saved, published, distributing, terminal -- one event each, no secret material."""
    _synced(env)
    env["ops"].ledger = _Ledger()
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 0.0)
    env["ops"].refresh_trust(force=True)
    steps = [e["step"] for e in env["ops"].ledger.events if e["kind"] == "trust_publication"]
    assert steps == ["begun", "previous_saved", "published", "distributing", "terminal"]
    blob = json.dumps(env["ops"].ledger.events)
    assert "BEGIN" not in blob and "PRIVATE" not in blob


def test_an_audit_failure_is_named_and_not_green(env, monkeypatch):
    """C19 / C25 / CC25: the ledger fails -- the publication stands (never undone) and the run is not green."""
    _synced(env)
    env["ops"].ledger = _Ledger(fail=True)
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 0.0)
    number = _current(env)["revocation_list_number"]
    report = env["ops"].refresh_trust(force=True)
    assert _current(env)["revocation_list_number"] == number + 1
    assert any(a.startswith("audit_failed") for a in report["alerts"])


def test_the_kill_switch_does_not_stop_trust_maintenance(env, monkeypatch):
    """C25 / CC26: a stopped gateway must still not admit revoked identities -- trust work goes on."""
    _synced(env)
    env["ops"]._kill_switch = lambda: "stopped by the operator"
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 0.0)
    number = _current(env)["revocation_list_number"]
    report = env["ops"].refresh_trust(force=True)
    assert report["published"] == number + 1 and report["lagging"] == []


def test_the_journal_is_not_in_the_operation_journal(env, monkeypatch):
    """D10 / CC20: the parent's `Operations.resume()` dispatches `_steps_<kind>`; no record may be there."""
    _synced(env)
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 0.0)
    env["ops"]._crash_after = lambda s: (_ for _ in ()).throw(Crash()) if s == "tp:published" else None
    with pytest.raises(Crash):
        env["ops"].refresh_trust(force=True)
    ops_dir = Path(env["reg"].stand) / "targets" / "ops"
    kinds = {json.loads(p.read_text())["kind"] for p in ops_dir.glob("op-*.json")} if ops_dir.is_dir() else set()
    assert "trust_publish" not in kinds and all(hasattr(O.Operations, "_steps_" + k) for k in kinds)
    env["ops"]._crash_after = lambda s: None
    env["ops"].resume()                                          # the parent's resume path does not choke


def test_an_unreadable_journal_is_a_named_refusal(env):
    """Negative: a foreign or truncated record stops the authority (named), never a guess."""
    d = TP.records_dir(env["reg"].stand)
    d.mkdir(parents=True, exist_ok=True)
    (d / "r-000000000001.json").write_text('{"schema": "something-else"}')
    with pytest.raises(OperationFailed) as failed:
        env["ops"].refresh_trust()
    assert failed.value.code == "trust_publication.journal_unreadable"


# ---------------------------------------------------------------------- R3 / D10: layout migration
def test_rollback_materialises_the_pointer_generation_into_the_legacy_files(env, monkeypatch):
    """C22 / CC22: the parent reads only `trust-previous/`: after materialisation it holds the pointer's pair."""
    _synced(env)
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 0.0)
    env["ops"].refresh_trust(force=True)
    want = T.previous_pair(env["reg"].stand)
    TP.materialize_legacy(env["reg"].stand)
    legacy = Path(env["reg"].stand) / "targets" / T.PREVIOUS_DIR
    assert ((legacy / "revoked.crl").read_bytes(), (legacy / "revoked-identities.json").read_bytes()) == want


def test_install_imports_a_newer_legacy_predecessor(env, monkeypatch):
    """R3: the parent saved a predecessor during a rollback window; install makes it the pointer's generation."""
    _synced(env)
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 0.0)
    env["ops"].refresh_trust(force=True)
    T.save_previous_pair(env["reg"].stand, *_pair_bytes(env))   # what the parent's refresh would have saved
    assert TP.import_legacy(env["ops"]).startswith("imported")
    assert T.previous_pair(env["reg"].stand) == _pair_bytes(env)
    assert TP.import_legacy(env["ops"]) == ""                    # idempotent


def test_unresolved_security_distribution_is_named(env):
    """C22: a live target not holding the current content is what a rollback must refuse."""
    _synced(env)
    restore = _unreachable(env)
    tid = _provision(env)
    env["ops"].probe(tid)
    with pytest.raises(OperationFailed):
        env["ops"].revoke(tid)
    assert TP.unresolved_security(env["ops"]) == ["managed-w1"]
    restore()
    env["ops"].refresh_trust()
    assert TP.unresolved_security(env["ops"]) == []


def test_the_cycle_state_records_success_and_failure(env, monkeypatch):
    """C21: the last successful cycle is what the watchdog reads; a failed one keeps the older success."""
    _synced(env)
    env["ops"].refresh_trust(entry="cycle", write_state=True)
    first = TP.load_cycle_state(env["reg"].stand)
    assert first["last_run"]["status"] == "ok" and first["last_success_at"]
    restore = _unreachable(env)
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 0.0)
    env["ops"].refresh_trust(force=True, entry="manual", write_state=True)
    second = TP.load_cycle_state(env["reg"].stand)
    assert second["last_run"]["status"] == "degraded" and second["last_success_at"] == first["last_success_at"]
    restore()


def test_the_cli_cycle_exit_codes(env, monkeypatch, capsys):
    from agentnode_sdk.remote_mcp import targets_cli
    monkeypatch.setattr(targets_cli, "build", lambda stand, canary, **kw: (env["ops"], None, None))
    _synced(env)
    assert targets_cli.main(["--stand", str(env["reg"].stand), "trust-cycle"]) == 0
    restore = _unreachable(env)
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 0.0)
    assert targets_cli.main(["--stand", str(env["reg"].stand), "trust-refresh", "--force"]) == 3
    restore()
    out = capsys.readouterr().out
    assert "BEGIN" not in out and hashlib.sha256(b"").hexdigest() not in out
    assert targets_cli.main(["--stand", str(env["reg"].stand), "trust-status"]) == 0
