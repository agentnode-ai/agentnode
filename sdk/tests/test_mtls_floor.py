"""mtls-revocation-time-r1: the time floor -- its arithmetic, its age, its boot, and who writes it.

Decision 5.7 and stage 5 (j1), (j2), (k), (l), (m), (n), (o), (p1), (p2), (q). The clocks are the
floor module's three seams (`_system_now`, `_monotonic`, `_boot`); nothing else is replaced.

## The oracle is the decision, transcribed

`decision_5_7` below is the seven steps of decision 5.7 in the decision's own names, one line per
step, in its order. The arithmetic tests compare `floor.advance` against it step by step -- not
against a restatement of what the steps are supposed to achieve.

## Each acceptance, one fatal condition

Every service-side test builds a floor that is valid in every respect but one, and shows the
control first: the same floor, with that one thing right, is accepted.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

from agentnode_sdk.pki import floor as floors
from agentnode_sdk.pki import identity as ids
from agentnode_sdk.pki.trust import TrustView
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout
from tests.test_mtls_transport import TEST_BOOT, World

DAY = 86400.0
FAR_AHEAD = 30 * DAY


def decision_5_7(boden_alt, verstrichen_bei_bootbeginn, monoton_anker, zugestanden_gesamt,
                 toleranz, monoton_jetzt, systemuhr_jetzt, thisUpdate_der_gueltigen_liste):
    """Decision 5.7, "Die Fortschreibung, Schritt fuer Schritt", as written there."""
    # 1. verstrichen_neu = verstrichen_bei_bootbeginn + (monoton_jetzt - monoton_anker)
    verstrichen_neu = verstrichen_bei_bootbeginn + (monoton_jetzt - monoton_anker)
    # 2. spielraum = verstrichen_neu + toleranz - zugestanden_gesamt      (nie negativ)
    spielraum = verstrichen_neu + toleranz - zugestanden_gesamt
    assert spielraum >= 0, "the decision says the headroom is never negative"
    # 3. uhr_vorschlag = max(0, systemuhr_jetzt - boden_alt)
    uhr_vorschlag = max(0.0, systemuhr_jetzt - boden_alt)
    # 4. uhr_zugelassen = min(uhr_vorschlag, spielraum)
    uhr_zugelassen = min(uhr_vorschlag, spielraum)
    # 5. boden_neu = max(boden_alt + uhr_zugelassen, thisUpdate_der_gueltigen_liste)
    boden_neu = boden_alt + uhr_zugelassen
    if thisUpdate_der_gueltigen_liste is not None:
        boden_neu = max(boden_neu, thisUpdate_der_gueltigen_liste)
    # 6. zugestanden_gesamt wird um uhr_zugelassen erhoeht, und um nichts sonst
    zugestanden_neu = zugestanden_gesamt + uhr_zugelassen
    # 7. verstrichen_gesamt wird auf verstrichen_neu gesetzt
    return boden_neu, zugestanden_neu, verstrichen_neu, uhr_zugelassen


def step(state, *, system_now, monotonic_now, boot=TEST_BOOT, list_this_update=None):
    """One root-run write, checked against the oracle before it is returned."""
    anchored_now = state.boot_id == boot
    anker = state.anchor_monotonic if anchored_now else monotonic_now
    bei_bootbeginn = state.elapsed_at_boot_start if anchored_now else state.elapsed_total
    expected = decision_5_7(state.floor, bei_bootbeginn, anker, state.granted_total,
                            state.tolerance_s, monotonic_now, system_now, list_this_update)
    new = floors.advance(state, system_now=system_now, monotonic_now=monotonic_now, boot=boot,
                         list_this_update=list_this_update)
    boden_neu, zugestanden_neu, verstrichen_neu, uhr_zugelassen = expected
    assert new.floor == pytest.approx(boden_neu, abs=1e-6), (
        "step 5 of decision 5.7 gives the floor %r, advance gave %r" % (boden_neu, new.floor))
    assert new.granted_total == pytest.approx(zugestanden_neu, abs=1e-6), (
        "step 6 of decision 5.7 gives granted_total %r, advance gave %r"
        % (zugestanden_neu, new.granted_total))
    assert new.elapsed_total == pytest.approx(verstrichen_neu, abs=1e-6), (
        "steps 1 and 7 of decision 5.7 give elapsed_total %r, advance gave %r"
        % (verstrichen_neu, new.elapsed_total))
    # The counter grows by EXACTLY what the clock put into the floor.
    assert new.granted_total - state.granted_total == pytest.approx(uhr_zugelassen, abs=1e-6), (
        "step 6 of decision 5.7: the counter must grow by exactly the clock's contribution")
    # And the invariant holds after every step.
    assert new.granted_total <= new.elapsed_total + new.tolerance_s + 1e-9, (
        "the invariant of decision 5.7 does not hold")
    return new


def adv(state, *, system_now, monotonic_now, boot=TEST_BOOT, list_this_update=None):
    """One root-run write WITHOUT the oracle -- for the tests whose own assertion is the one
    that must speak when the property they are about is broken, not the step-by-step check."""
    return floors.advance(state, system_now=system_now, monotonic_now=monotonic_now, boot=boot,
                          list_this_update=list_this_update)


START = 1_790_000_000.0


def fresh(tolerance=600.0, max_age=900.0, role="worker"):
    return floors.initial(role, START, tolerance_s=tolerance, max_age_s=max_age)


# ====================================================================== (l) (m) (n) (o) (q)

class TestTheArithmetic:

    def test_l_many_quick_updates_with_a_clock_far_ahead(self):
        """Hundreds of writes a millisecond apart while the system clock claims a month more.
        The clock's total contribution never exceeds the elapsed time plus the one tolerance."""
        state = fresh()
        mono = 100.0
        state = step(state, system_now=START + FAR_AHEAD, monotonic_now=mono)
        for i in range(500):
            mono += 0.001
            state = step(state, system_now=START + FAR_AHEAD + i, monotonic_now=mono)
        assert state.granted_total <= state.elapsed_total + state.tolerance_s + 1e-9
        assert state.floor <= START + state.elapsed_total + state.tolerance_s + 1e-6, (
            "the floor moved further than time that ran plus one tolerance")

    def test_m_restarting_the_process_resets_neither_the_anchor_nor_the_tolerance(self):
        """Each root run is a new process that reads the file. Within one boot the anchor it
        reads is the anchor it keeps, and the tolerance it finds spent stays spent."""
        state = adv(fresh(), system_now=START + FAR_AHEAD, monotonic_now=50.0)
        anchor = state.anchor_monotonic
        for mono in (60.0, 70.0, 80.0):
            state = floors.parse(state.to_bytes())            # a new process reads the file
            state = adv(state, system_now=START + FAR_AHEAD, monotonic_now=mono)
            assert state.anchor_monotonic == anchor, "the anchor moved within one boot"
        assert state.elapsed_total == pytest.approx(30.0)
        assert state.granted_total == pytest.approx(30.0 + 600.0), (
            "a restart of the process gave the clock a second tolerance")

    def test_n_quick_machine_restarts_move_the_floor_no_further_than_one(self):
        """The same running time, once spread over many boots and once in a single boot, with a
        clock far ahead: many restarts together do not move the floor further than one."""
        many = adv(fresh(), system_now=START + FAR_AHEAD, monotonic_now=10.0, boot="b0")
        for n in range(1, 8):
            many = adv(many, system_now=START + FAR_AHEAD, monotonic_now=10.0, boot="b%d" % n)
            many = adv(many, system_now=START + FAR_AHEAD, monotonic_now=15.0, boot="b%d" % n)
        one = adv(fresh(), system_now=START + FAR_AHEAD, monotonic_now=10.0, boot="c0")
        one = adv(one, system_now=START + FAR_AHEAD, monotonic_now=10.0 + 7 * 5.0, boot="c0")
        assert many.elapsed_total == pytest.approx(one.elapsed_total)
        assert many.floor <= one.floor + 1e-6, (
            "restarting the machine gave the clock more than running it did: %.1f > %.1f"
            % (many.floor - START, one.floor - START))

    def test_o_a_correct_clock_on_a_running_machine_moves_the_floor(self):
        """The control that keeps a floor which never moves from passing (n): with the clock
        right and the machine running, the floor follows the time."""
        state = adv(fresh(tolerance=0.0), system_now=START, monotonic_now=1000.0)
        for t in (60.0, 120.0, 600.0, 3600.0):
            state = adv(state, system_now=START + t, monotonic_now=1000.0 + t)
            assert state.floor == pytest.approx(START + t, abs=1.0), (
                "the floor stood still while the machine ran and the clock was right")

    def test_q_a_recovery_moves_the_floor_and_leaves_the_counters(self):
        """Root sets a floor that stands too far ahead back to a signed value. The lifetime
        counters are untouched, so a clock set far ahead afterwards gets no second tolerance."""
        state = adv(fresh(), system_now=START + FAR_AHEAD, monotonic_now=5.0)
        spent = (state.elapsed_total, state.granted_total)
        recovered = floors.recover(state, START)
        assert (recovered.elapsed_total, recovered.granted_total) == spent, (
            "a recovery reset the lifetime counters")
        assert recovered.floor == START
        after = adv(recovered, system_now=START + FAR_AHEAD, monotonic_now=6.0)
        assert after.floor - START <= 1.0 + 1e-6, (
            "after a recovery the far-ahead clock moved the floor by %.1f s: a second tolerance"
            % (after.floor - START))

    def test_a_signed_list_raises_the_floor_without_counting_as_the_clock(self):
        state = step(fresh(tolerance=0.0), system_now=START, monotonic_now=1.0)
        lifted = step(state, system_now=START, monotonic_now=2.0,
                      list_this_update=START + DAY)
        assert lifted.floor == START + DAY
        # The clock offered nothing (it reads START, the floor was START); the list lifted the
        # floor a whole day, and the clock's counter did not move by any of it.
        assert lifted.granted_total == state.granted_total


# ====================================================================== (p1) (p2) (j2)

def _judge(state, *, boot=TEST_BOOT, monotonic_now):
    return floors.judge(state.to_bytes(), role=state.role, boot=boot,
                        monotonic_now=monotonic_now)


class TestTheAgeAndTheBoot:

    def test_p1_a_floor_nobody_keeps_up_stops_the_service_and_setting_the_clock_back_does_not_help(
            self, monkeypatch):
        state = adv(fresh(max_age=900.0), system_now=START, monotonic_now=10_000.0)
        # The system clock set back to the very moment of the write -- and that wall-clock time
        # is right there in the file, so an age judged by it would be zero.
        monkeypatch.setattr(floors, "_system_now", lambda: START)
        # The control: inside its age, the floor is used.
        assert _judge(state, monotonic_now=10_000.0 + 899.0) == state.floor
        # One second past it. The deadline did not move with the clock.
        with pytest.raises(floors.FloorUnusable) as caught:
            _judge(state, monotonic_now=10_000.0 + 901.0)
        assert caught.value.check == floors.TOO_OLD

    def test_p2_after_a_restart_nothing_is_served_until_the_root_run_writes_in_this_boot(self):
        state = adv(fresh(), system_now=START, monotonic_now=10.0, boot="before")
        assert _judge(state, boot="before", monotonic_now=11.0) == state.floor   # the control
        for restart in ("after-1", "after-2", "after-3"):               # a broken writer
            # The new boot's monotonic clock happens to read inside the old write's window --
            # the case an age check alone would wave through.
            with pytest.raises(floors.FloorUnusable) as caught:
                _judge(state, boot=restart, monotonic_now=20.0)
            assert caught.value.check == floors.OTHER_BOOT
        written = adv(state, system_now=START + 5, monotonic_now=2.0, boot="after-3")
        assert _judge(written, boot="after-3", monotonic_now=3.0) == written.floor

    def test_the_initial_state_is_not_a_floor_anyone_may_use(self):
        with pytest.raises(floors.FloorUnusable) as caught:
            _judge(fresh(), monotonic_now=1.0)
        assert caught.value.check == floors.NOT_WRITTEN

    @pytest.mark.parametrize("damage", ["missing", "empty", "torn", "extra-field", "wrong-role"])
    def test_j2_a_missing_or_unreadable_floor_is_never_a_fresh_start(self, damage):
        state = adv(fresh(), system_now=START, monotonic_now=10.0)
        good = state.to_bytes()
        assert floors.judge(good, role="worker", boot=TEST_BOOT, monotonic_now=11.0)  # control
        data = {"missing": None, "empty": b"", "torn": good[: len(good) // 2],
                "extra-field": good.replace(b'"floor"', b'"extra": 1, "floor"'),
                "wrong-role": good}[damage]
        role = "gateway" if damage == "wrong-role" else "worker"
        with pytest.raises(floors.FloorUnusable) as caught:
            floors.judge(data, role=role, boot=TEST_BOOT, monotonic_now=11.0)
        assert caught.value.check in (floors.MISSING, floors.UNREADABLE)

    def test_j2_the_root_run_does_not_start_a_floor_that_went_missing(self, tmp_path):
        world = World(tmp_path)
        path = floors.path_for(world.floor_dir, "worker")
        path.unlink()
        report = world.tick()
        assert not path.exists(), "the root run started a new floor on its own"
        assert "gateway" in report["floors"]
        assert "worker" not in report["floors"]


# ====================================================================== the service side, live

def _refused_check(world: World, folder, *, role="gateway", accept=("w1",)):
    """What the gateway side says when it tries to reach a live worker door now."""
    from agentnode_sdk.worker import WorkerUnreachable
    from agentnode_sdk.worker.remote import TlsWorker
    from tests.test_mtls_transport import KEY

    client = TlsWorker(world.door.address, KEY, world.settings(folder, set(accept), role=role),
                       say=lambda text: None)
    try:
        client.confirm_reachable()
        return None
    except WorkerUnreachable as refused:
        return str(refused)


class TestTheServicesJudgeByTheFloor:

    def _world(self, tmp_path):
        from tests.test_mtls_transport import Door

        world = World(tmp_path)
        world.gateway = world.service("gateway", "g1")
        world.worker = world.service("worker", "w1")
        world.door = Door(world, world.worker, {"g1"}, label="w1")
        return world

    def test_a_service_whose_floor_aged_out_refuses_every_connection(self, tmp_path, monkeypatch):
        world = self._world(tmp_path)
        try:
            assert _refused_check(world, world.gateway) is None                   # the control
            now = floors._monotonic()
            monkeypatch.setattr(floors, "_monotonic", lambda: now + 901.0)
            said = _refused_check(world, world.gateway)
            assert said and floors.TOO_OLD in said, said
        finally:
            world.door.close()

    def test_a_service_after_a_restart_refuses_until_the_root_run_wrote(self, tmp_path,
                                                                         monkeypatch):
        world = self._world(tmp_path)
        try:
            monkeypatch.setattr(floors, "_boot", lambda: "another-boot")
            said = _refused_check(world, world.gateway)
            assert said and floors.OTHER_BOOT in said, said
            world.tick()
            assert _refused_check(world, world.gateway) is None
        finally:
            world.door.close()

    def test_a_missing_floor_refuses_and_is_not_restarted(self, tmp_path):
        world = self._world(tmp_path)
        try:
            floors.path_for(world.floor_dir, "gateway").unlink()
            said = _refused_check(world, world.gateway)
            assert said and floors.MISSING in said, said
        finally:
            world.door.close()


# ====================================================================== (k) the service never writes

_WATCHING: dict = {"on": False, "dir": None, "seen": []}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def _under(path) -> bool:
    try:
        return Path(os.fsdecode(path)).resolve().parent == _WATCHING["dir"]
    except (TypeError, ValueError, OSError):
        return False


def _audit(event: str, args) -> None:
    if not _WATCHING["on"] or _WATCHING["dir"] is None:
        return
    if event == "open" and args and _under(args[0]):
        mode, flags = args[1], args[2] if len(args) > 2 else 0
        writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & _WRITE_FLAGS)
        if writes:
            _WATCHING["seen"].append(("open for writing", os.fsdecode(args[0]), mode, flags))
    elif event in ("os.remove", "os.rename", "os.replace", "os.truncate", "os.chmod",
                   "os.chown", "os.link", "os.symlink") and args and _under(args[0]):
        _WATCHING["seen"].append((event, os.fsdecode(args[0])))


sys.addaudithook(_audit)


class TestTheServiceDoesNotTryToWriteTheFloor:

    def test_k_a_complete_run_opens_the_floor_for_reading_only(self, tmp_path):
        """A job over mutual TLS, through a real gateway to a TLS worker, re-evaluated while it
        runs -- recorded at the interpreter's audit hook, which sees every open, remove, rename,
        replace and truncate on the floor's directory whatever code made it and whether or not
        it succeeded. The root run is not in the recording: it ran before, and nothing else here
        writes the floor. The recording has to show reads (so it was looking) and no write."""
        from agentnode_sdk.worker.remote import TlsWorker
        from tests.test_mtls_parity import a_gateway_over, submit_and_wait
        from tests.test_mtls_transport import KEY, Door

        world = World(tmp_path / "pki")
        gateway_dir = world.service("gateway", "g1")
        door = Door(world, world.service("worker", "w1"), {"g1"}, label="w1")
        client = TlsWorker(door.address, KEY, world.settings(gateway_dir, {"w1"}),
                           say=lambda text: None)
        gw = a_gateway_over(tmp_path / "gw", client)
        reads: list = []

        def counting(event, args):
            if _WATCHING["on"] and event == "open" and args and _under(args[0]):
                reads.append(os.fsdecode(args[0]))

        sys.addaudithook(counting)
        _WATCHING.update(on=True, dir=world.floor_dir.resolve(), seen=[])
        try:
            record = submit_and_wait(gw, "k-run")
            done = threading.Event()
            threading.Timer(0.5, done.set).start()
            done.wait()                                       # let a few re-evaluations pass
        finally:
            _WATCHING["on"] = False
            gw.close()
            door.close()
        assert record.get("state") == "finished" and record.get("exit_code") == 0, record
        assert reads, "the recording saw no read of the floor, so it proves nothing"
        assert _WATCHING["seen"] == [], (
            "a service tried to write the floor: %r" % (_WATCHING["seen"][:5],))


# ====================================================================== (j1) as another account

def _account():
    name = os.environ.get("AGENTNODE_FLOOR_TEST_ACCOUNT", "nobody")
    try:
        import pwd

        entry = pwd.getpwnam(name)
        return name, entry.pw_uid, entry.pw_gid
    except (ImportError, KeyError):
        return name, None, None


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() != 0,
                    reason="NO TEST RAN: (j1) needs root, to make a root-owned floor and then act "
                           "as another account against it; it runs on the Linux alpha as root")
class TestTheFloorIsNotAServicesToChange:

    def test_j1_deleting_overwriting_and_truncating_fail_for_a_service_account(self, tmp_path):
        """The floor exactly as the product sets it up, in a directory another account can
        reach -- and that account tries all three. Each must fail with the system's own error,
        and the file must be byte for byte what it was."""
        import subprocess

        name, uid, gid = _account()
        assert uid is not None, "no account %r to act as" % name
        base = Path("/tmp") / ("agentnode-floor-j1-%d" % os.getpid())
        base.mkdir(mode=0o755)
        os.chmod(base, 0o755)
        try:
            world = World(base)
            os.chmod(world.root, 0o755)
            path = floors.path_for(world.floor_dir, "worker")
            before = path.read_bytes()
            attempt = (
                "import os, sys\n"
                "p = sys.argv[1]\n"
                "for what, act in (('delete', lambda: os.remove(p)),\n"
                "                  ('overwrite', lambda: open(p, 'r+b').write(b'{}')),\n"
                "                  ('truncate', lambda: os.truncate(p, 0)),\n"
                "                  ('replace', lambda: open(os.path.join(os.path.dirname(p),\n"
                "                                   'mine.floor'), 'wb').close())):\n"
                "    try:\n"
                "        act(); print(what, 'SUCCEEDED')\n"
                "    except OSError as e:\n"
                "        print(what, 'failed:', e.__class__.__name__, e.errno, e.strerror)\n")

            def become():
                os.setgroups([])
                os.setgid(gid)
                os.setuid(uid)

            done = subprocess.run([sys.executable, "-c", attempt, str(path)], preexec_fn=become,
                                  capture_output=True, text=True, timeout=60)
            said = done.stdout.strip().splitlines()
            print("\n".join(["as %s (uid %d):" % (name, uid)] + said))
            assert len(said) == 4, done.stderr
            for line in said:
                assert "failed:" in line and ("Permission denied" in line
                                              or "Operation not permitted" in line), said
            assert path.read_bytes() == before
            st = path.stat()
            assert (st.st_uid, oct(st.st_mode & 0o777)) == (0, "0o644")
            dst = world.floor_dir.stat()
            assert (dst.st_uid, oct(dst.st_mode & 0o777)) == (0, "0o755")
        finally:
            import shutil

            shutil.rmtree(base, ignore_errors=True)


class TestTheTrustViewTellsItsReasonsApart:

    def test_an_unreadable_floor_is_named_as_such(self, tmp_path):
        view = TrustView(anchor=b"", revocation_list=None, floor=None,
                         floor_problem="PermissionError", role="worker")
        with pytest.raises(ids.PeerRefused) as caught:
            view.effective_time()
        assert caught.value.check == floors.UNREADABLE
