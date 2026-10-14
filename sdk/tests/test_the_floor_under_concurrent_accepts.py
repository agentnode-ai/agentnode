"""EG13: the replay floor under concurrent accepts, where the two-machine acceptance found it break.

The worker's TLS listener runs one thread per accepted connection (`worker/tls.py`), and every valid
request moves the ONE shared replay floor forward through `Floor.accepted()` (`worker/protocol.py`). The
BETA-5 exact-head acceptance of `ef5f01f` found that two near-simultaneous valid requests race there: on
`ef5f01f` `Floor.accepted()` does an unlocked check-then-persist-then-set on `_highest`, and `_persist()`
writes one FIXED sibling pathname `<floor>.new` before `os.replace()`. Two overlapping writers both write
that one temp; one `os.replace()` finds it already gone and raises -> the product's `CannotRememberTheFloor`
(an `OSError`), which the gateway reports as a lost worker connection and the run never comes up. A quieter
second fault: the unlocked `_highest` read-modify-write can move the floor BACKWARDS.

These are the FROZEN final concurrency tests for the repair (written before any product change, amended per
the Codex Q0015 design review). Invariants:

  * I1 -- concurrent valid accepts never raise CannotRememberTheFloor (a supported workload must not drop);
  * I2 -- `highest` after N concurrent accepts equals the greatest moment, never less (no lost update);
  * I3 -- persistence is collision-free (unique temp per write) and crash-safe (fsync then atomic rename,
    so a reader ever only sees a whole old or a whole new value), with the temp cleaned up on failure;
  * I4 -- a restart reads back that greatest moment;
  * I5 -- a GENUINE write failure still raises CannotRememberTheFloor (the worker-startup distinction),
    while mere lock contention does not.

SCOPE (Codex Q0015): the repair assumes the product's contract of ONE worker process with ONE shared
`Bench.floor` -- a per-instance lock synchronises that one object. Unique temp names also stop two separate
`Floor` objects colliding on a pathname, but a per-instance lock does not stop two separate instances
replacing a greater value with a smaller one; that is out of scope because the product never makes two. The
`Seen` nonce cache is ALSO shared and unlocked (a separate check-and-insert race); it is tracked as its own
issue and is deliberately NOT folded into this EG13 repair.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from agentnode_sdk.worker import protocol as wire


# --------------------------------------------------------------------------- helpers

def _hammer(floor, moments, *, workers=16, call=None):
    """Call `call(floor, m)` (default `floor.accepted`) for every m, across `workers` threads at once.

    Every thread waits on one barrier so the calls overlap as tightly as the machine allows, which is what
    makes the shared-temp and the unlocked read-modify-write collide. Returns the exceptions raised.
    """
    if call is None:
        def call(fl, m):
            fl.accepted(float(m))
    errors: list = []
    lock = threading.Lock()
    start = threading.Barrier(workers)
    items = list(moments)

    def run(chunk):
        start.wait()
        for m in chunk:
            try:
                call(floor, m)
            except BaseException as exc:                      # noqa: BLE001 - the test records them
                with lock:
                    errors.append(exc)

    chunks = [items[i::workers] for i in range(workers)]
    threads = [threading.Thread(target=run, args=(c,)) for c in chunks]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def _persisted(path) -> float:
    return float(json.loads(Path(path).read_text(encoding="utf-8"))["highest"])


def _leftover_temps(path) -> list:
    p = Path(path)
    return [q.name for q in p.parent.iterdir() if q.name != p.name]


_MOMENTS = [1000.0 + i for i in range(240)]
_REPEATS = 25


# --------------------------------------------------------------------------- I1, I2, I4, I5

class TestTheFloorUnderConcurrentAccepts:

    def test_concurrent_accepts_never_lose_the_floor(self, tmp_path):
        """I1/I2/I3: many valid accepts at once -- none raises, the floor is the greatest, the file
        agrees, and no temp is left behind."""
        floor = wire.Floor(tmp_path / "replay-floor.json")
        errors = _hammer(floor, _MOMENTS)
        assert errors == [], (
            "concurrent accepts raised %d time(s); first: %r"
            % (len(errors), errors[0] if errors else None))
        top = max(_MOMENTS)
        assert floor.highest == top, "the in-memory floor is %r, not the greatest %r" % (floor.highest, top)
        assert _persisted(floor.path) == top, "the persisted floor is not the greatest accepted moment"
        assert _leftover_temps(floor.path) == [], (
            "writers left temp files behind: %r" % _leftover_temps(floor.path))

    def test_repeated_concurrency_never_loses_the_floor(self, tmp_path):
        """I1/I2 across many repeats, because the race is probabilistic."""
        top = max(_MOMENTS)
        for i in range(_REPEATS):
            floor = wire.Floor(tmp_path / ("replay-floor-%d.json" % i))
            errors = _hammer(floor, _MOMENTS)
            assert errors == [], "repeat %d raised: %r" % (i, errors[:1])
            assert floor.highest == top and _persisted(floor.path) == top, (
                "repeat %d left the floor at %r / file %r, not %r"
                % (i, floor.highest, _persisted(floor.path), top))

    def test_the_floor_never_runs_backwards_under_concurrency(self, tmp_path):
        """I2: moments offered out of order from many threads end at the greatest, never below."""
        import random

        shuffled = list(_MOMENTS)
        random.Random(1234).shuffle(shuffled)
        top = max(_MOMENTS)
        for i in range(_REPEATS):
            floor = wire.Floor(tmp_path / ("floor-back-%d.json" % i))
            _hammer(floor, shuffled)
            assert floor.highest == top, "repeat %d: floor ran back to %r" % (i, floor.highest)
            assert _persisted(floor.path) == top, "repeat %d: the file ran back to %r" % (
                i, _persisted(floor.path))

    def test_a_restart_reads_back_the_greatest_concurrent_accept(self, tmp_path):
        """I4: after concurrent accepts a fresh Floor (as on a restart) reads the greatest."""
        path = tmp_path / "replay-floor.json"
        _hammer(wire.Floor(path), _MOMENTS)
        after_restart = wire.Floor(path)
        assert after_restart.highest == max(_MOMENTS)

    def test_a_genuine_write_failure_still_raises_cannot_remember(self, tmp_path):
        """I5: a floor whose directory cannot hold the file still raises CannotRememberTheFloor."""
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        floor = wire.Floor(blocker / "replay-floor.json")
        with pytest.raises(wire.CannotRememberTheFloor):
            floor.accepted(1000.0)

    def test_concurrent_accepts_do_not_hide_a_genuine_write_failure(self, tmp_path):
        """I5 under concurrency: an unwritable floor is reported, never swallowed by the lock."""
        blocker = tmp_path / "also-not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        floor = wire.Floor(blocker / "replay-floor.json")
        errors = _hammer(floor, _MOMENTS[:40], workers=8)
        assert errors, "an unwritable floor under concurrency raised nothing"
        assert all(isinstance(e, wire.CannotRememberTheFloor) for e in errors), (
            "a write failure was reported as something other than CannotRememberTheFloor: %r"
            % [type(e).__name__ for e in errors])


# --------------------------------------------------------------------------- I3, mechanism by mechanism
#
# Codex Q0015: the concurrent-outcome tests above do not make every part of I3 independently
# regression-sensitive. These do: each asserts one mechanism (unique temp, fsync-before-rename, cleanup on
# failure, whole-file reads, the probe cannot lower the floor, a file-sync failure is fatal, a directory-sync
# failure is not), so removing that mechanism alone turns one of these red.

class TestI3MechanismsAreRegressionSensitive:

    def test_persist_uses_a_unique_temp_per_overlapping_call(self, tmp_path, monkeypatch):
        """The unique-temp protection, isolated: overlapping writes use DISTINCT temp names in the
        floor's own directory, so one writer can never delete or rename another's temp -- the fixed
        `<floor>.new` collision the acceptance hit. Asserted on the temp NAMES, not on the outcome,
        because concurrent rename onto one target is the LOCK's concern (and on Windows raises for a
        different reason) -- this test is only about the names being collision-free."""
        floor = wire.Floor(tmp_path / "replay-floor.json")
        seen: list = []
        seen_lock = threading.Lock()
        real_mkstemp = wire.tempfile.mkstemp

        def spy(*args, **kwargs):
            fd, path = real_mkstemp(*args, **kwargs)
            with seen_lock:
                seen.append(path)
            return fd, path

        monkeypatch.setattr(wire.tempfile, "mkstemp", spy)
        _hammer(floor, _MOMENTS, call=lambda fl, m: fl._persist(float(m)))
        assert seen, "no unique temp was created; the write did not go through a unique temp at all"
        assert len(seen) == len(set(seen)), "two overlapping writes shared a temp name"
        for p in seen:
            assert Path(p).parent == floor.path.parent, (
                "a temp was created outside the floor's own directory: %r" % p)
        assert _leftover_temps(floor.path) == [], "a temp was left behind by _persist"

    def test_fsync_happens_before_the_rename(self, tmp_path, monkeypatch):
        """Crash-safety: the file is flushed to disk before it is put in place."""
        events: list = []
        real_fsync, real_replace = os.fsync, os.replace
        monkeypatch.setattr(wire.os, "fsync", lambda fd: (events.append("fsync"), real_fsync(fd))[1])
        monkeypatch.setattr(wire.os, "replace",
                            lambda a, b: (events.append("replace"), real_replace(a, b))[1])
        wire.Floor(tmp_path / "replay-floor.json").accepted(1000.0)
        assert "fsync" in events, "the floor was renamed into place without being fsync'd first"
        assert events.index("fsync") < events.index("replace"), (
            "os.replace happened before the file was fsync'd: %r" % events)

    def test_a_rename_failure_is_cleaned_up_and_classified(self, tmp_path, monkeypatch):
        """A failure after the temp exists raises CannotRememberTheFloor AND leaves no temp behind."""
        floor = wire.Floor(tmp_path / "replay-floor.json")
        monkeypatch.setattr(wire.os, "replace",
                            lambda a, b: (_ for _ in ()).throw(OSError("rename refused")))
        with pytest.raises(wire.CannotRememberTheFloor):
            floor.accepted(1000.0)
        assert _leftover_temps(floor.path) == [], (
            "a failed write left a temp behind: %r" % _leftover_temps(floor.path))

    def test_a_file_sync_failure_is_fatal(self, tmp_path, monkeypatch):
        """A failure fsync'ing the floor file itself is a write failure, classified as such."""
        floor = wire.Floor(tmp_path / "replay-floor.json")
        monkeypatch.setattr(wire.os, "fsync",
                            lambda fd: (_ for _ in ()).throw(OSError("fsync refused")))
        with pytest.raises(wire.CannotRememberTheFloor):
            floor.accepted(1000.0)

    def test_a_directory_sync_failure_is_not_fatal(self, tmp_path, monkeypatch):
        """Per the platform policy: the file write/fsync/rename are fatal on failure, but the best-effort
        directory sync that follows the rename is not -- its failure (e.g. Windows) is swallowed, because
        by then the new floor is already visible."""
        floor = wire.Floor(tmp_path / "replay-floor.json")
        real_fsync = os.fsync
        calls = {"n": 0}

        def only_dir_fsync_fails(fd):
            calls["n"] += 1
            if calls["n"] == 1:
                return real_fsync(fd)          # the file fsync (first) succeeds
            raise OSError("directory sync unsupported")   # the directory fsync (after) fails

        monkeypatch.setattr(wire.os, "fsync", only_dir_fsync_fails)
        floor.accepted(1000.0)                 # must NOT raise
        assert _persisted(floor.path) == 1000.0
        assert floor.highest == 1000.0

    def test_a_continuous_reader_never_sees_a_torn_floor(self, tmp_path):
        """Crash-safety as atomicity: while accepts hammer the floor, a reader only ever sees a whole,
        well-formed document -- never a partial write."""
        path = tmp_path / "replay-floor.json"
        floor = wire.Floor(path)
        floor.accepted(999.0)                  # so the file exists before the reader starts
        stop = threading.Event()
        torn: list = []

        def read_forever():
            while not stop.is_set():
                try:
                    raw = Path(path).read_text(encoding="utf-8")
                    if raw:
                        float(json.loads(raw)["highest"])
                except (ValueError, KeyError) as bad:
                    torn.append(repr(bad))     # a partial/parse failure IS a torn read
                except OSError:
                    pass                       # a rename window (incl. Windows sharing) is not a torn read

        reader = threading.Thread(target=read_forever)
        reader.start()
        try:
            _hammer(floor, _MOMENTS)
        finally:
            stop.set()
            reader.join()
        assert torn == [], "a reader saw a torn floor file: %r" % torn[:3]

    def test_the_startup_probe_cannot_lower_the_floor_against_an_advance(self, tmp_path):
        """can_be_written() writes back the value it holds; racing it against advancing accepts must not
        let it restore a stale, lower value over a higher one. The lock on both is what guarantees it."""
        path = tmp_path / "replay-floor.json"
        top = max(_MOMENTS)
        for i in range(_REPEATS):
            floor = wire.Floor(Path(str(path) + str(i)))
            floor.accepted(_MOMENTS[0])

            def probe():
                for _ in range(len(_MOMENTS)):
                    floor.can_be_written()

            t = threading.Thread(target=probe)
            t.start()
            _hammer(floor, _MOMENTS, workers=8)
            t.join()
            assert floor.highest == top and _persisted(floor.path) == top, (
                "repeat %d: the probe left the floor at %r / file %r, not %r"
                % (i, floor.highest, _persisted(floor.path), top))


# --------------------------------------------------------------------------- the counter-checks
#
# Each reintroduces exactly ONE defect the repair removes and shows the invariant's own assertion go red for
# that defect alone. The broken behaviour is defined HERE, so these do not depend on the product keeping a
# seam, and they remain red on the REPAIRED base class (Codex Q0015: the fixed-temp one must exercise
# `_persist` directly, or the repaired lock would serialise it and it would stop colliding).

class _FixedTempFloor(wire.Floor):
    """The `ef5f01f` persistence: one FIXED sibling temp name, so two writers collide on it."""

    def _persist(self, value: float) -> None:                 # noqa: D401
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".new")               # THE DEFECT: shared across writers
            tmp.write_text(json.dumps({"highest": float(value)}), encoding="utf-8")
            os.replace(tmp, self.path)
        except (OSError, ValueError) as exc:
            raise wire.CannotRememberTheFloor(str(exc)) from exc


class _UnlockedFloor(wire.Floor):
    """The `ef5f01f` read-modify-write: no lock, so `_highest` can be lowered by a lost update."""

    def accepted(self, issued_at: float) -> None:
        issued_at = float(issued_at)
        if issued_at <= self._highest:                        # THE DEFECT: unlocked check-then-set
            return
        import time as _t

        seen = self._highest
        _t.sleep(0)
        wire.Floor._persist(self, issued_at)                  # the REPAIRED (unique-temp) persist
        _t.sleep(0)
        if issued_at > seen:
            self._highest = issued_at


class TestTheCounterChecksGoRed:

    def test_a_fixed_temp_name_makes_concurrent_persist_collide(self, tmp_path):
        """The collision-free protection, red on its own: with one shared temp name, concurrent _persist
        DOES raise CannotRememberTheFloor. Exercised through `_persist` directly so the repaired lock on
        `accepted()` cannot hide the collision (Codex Q0015)."""
        saw_raise = False
        for i in range(_REPEATS):
            floor = _FixedTempFloor(tmp_path / ("fixed-%d.json" % i))
            errors = _hammer(floor, _MOMENTS, call=lambda fl, m: fl._persist(float(m)))
            if any(isinstance(e, wire.CannotRememberTheFloor) for e in errors):
                saw_raise = True
                break
        assert saw_raise, (
            "the fixed-temp floor never collided; this counter-check proves nothing about the "
            "collision-free persistence the repair adds")

    def test_an_unlocked_accept_runs_the_floor_backwards(self, tmp_path):
        """The no-backwards protection, red on its own: without the lock a lost update leaves the floor
        below the greatest accepted moment at least once across the repeats."""
        import random

        shuffled = list(_MOMENTS)
        random.Random(99).shuffle(shuffled)
        top = max(_MOMENTS)
        saw_low = False
        for i in range(_REPEATS):
            floor = _UnlockedFloor(tmp_path / ("unlocked-%d.json" % i))
            _hammer(floor, shuffled)
            if floor.highest < top or _persisted(floor.path) < top:
                saw_low = True
                break
        assert saw_low, (
            "the unlocked floor never lost an update; this counter-check proves nothing about the "
            "synchronisation the repair adds")
