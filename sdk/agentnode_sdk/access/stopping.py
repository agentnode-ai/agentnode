"""Who actually carries out a cancellation, and what stops that being a way to exhaust a gateway.

Cancelling is not instant. The sandbox has to be torn down and CONFIRMED gone, and that
confirmation is the only reason a terminal state is worth anything. Doing it on the caller's
thread meant the caller waited for it; doing it on a thread created per call meant anybody who
could ask for a cancellation could ask for a thread, which is a worse arrangement wearing better
clothes.

So cancellations are carried out here, by a fixed number of hands that this object owns:

* **Bounded.** A fixed pool, created once, not one thread per request. The queue has a size, and
  a full queue is refused rather than absorbed. There is no number of requests that produces an
  unbounded number of anything.
* **One per run.** A run has at most one cancellation in flight. A second request for the same
  run joins the first and gets the same answer -- not a second stop, and not an error.
* **Owned.** Nothing is started until the first cancellation is asked for, so a gateway that
  never cancels anything has no threads. Everything started is joined by `close()`.
* **Durable.** What was asked for is written down before it is attempted, and only forgotten once
  cleanup is confirmed. A gateway killed mid-cancellation picks it up again on the way back,
  because the container it was tearing down does not disappear because the process did.
* **Rate limited.** A NEW stop costs budget; joining an existing one does not. So repeating a
  request is free -- which it must be, since repeating is how a client polls -- while a storm
  across many runs is bounded.

The one thing this does NOT do is shorten the confirmation. Nothing here makes a run terminal;
`carry_out` does that only when the gateway has verified the sandbox is gone, and a stop that
failed leaves the run exactly as honest as it was.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time

#: Queued, being worked on, confirmed gone, or attempted and failed.
QUEUED, WORKING, SETTLED, GAVE_UP = "queued", "working", "settled", "gave_up"
#: The two that mean "there is a cancellation happening right now".
IN_FLIGHT = (QUEUED, WORKING)


class TooManyStops(Exception):
    """A cancellation was refused because of a limit, not because of the run."""

    def __init__(self, because: str, what_to_do: str = "") -> None:
        super().__init__(because)
        self.because = because
        self.what_to_do = what_to_do


class Stop:
    """One cancellation. Repeated requests for the same run share exactly one of these."""

    __slots__ = ("run_id", "asked_at", "asked_by", "attempts", "state", "settled", "problem")

    def __init__(self, run_id: str, asked_by: str, at: float) -> None:
        self.run_id = str(run_id)
        self.asked_at = at
        self.asked_by = asked_by
        self.attempts = 0
        self.state = QUEUED
        #: True only when the gateway confirmed the sandbox is gone. None until then -- which is
        #: different from False, and a client is told which.
        self.settled = None
        self.problem = ""

    @property
    def in_flight(self) -> bool:
        return self.state in IN_FLIGHT

    def as_dict(self) -> dict:
        return {"run_id": self.run_id, "stopping": self.in_flight, "attempts": self.attempts,
                "cleanup_verified": self.settled, "problem": self.problem}


class Stopping:
    """The bounded owner of every cancellation this gateway carries out."""

    #: Two is enough to keep one slow teardown from blocking every other run, and small enough
    #: that the pool is never the thing under load. It is a constant rather than a setting
    #: because a number a deployment can raise is a number a deployment will raise.
    HANDS = 2
    #: A full queue is a gateway already stopping more runs than it has any business having.
    ROOM = 64
    #: New stops one device may start in a window. Joining an existing stop is free.
    PER_DEVICE = 30
    WINDOW_SECONDS = 60.0
    #: How long `close()` waits for a hand to notice it should finish.
    GOODBYE_SECONDS = 10.0

    def __init__(self, root, carry_out, *, hands: int = HANDS, room: int = ROOM,
                 clock=time.time) -> None:
        #: `carry_out(run_id) -> bool`. True means the gateway confirmed the sandbox is gone.
        self._carry_out = carry_out
        self._journal = os.path.join(str(root), "stopping.json")
        self._clock = clock
        self._hands_wanted = max(1, int(hands))
        self._work: queue.Queue = queue.Queue(maxsize=max(1, int(room)))
        self._lock = threading.RLock()
        self._stops: dict = {}
        self._recent: dict = {}
        self._hands: list = []
        self._closed = False

    # ------------------------------------------------------------------ asking

    def ask(self, run_id: str, by: str = "") -> Stop:
        """Ask for a run to be stopped. Idempotent while one is in flight.

        Raises `TooManyStops` when a limit is reached -- never when the request is simply a
        repeat, because a client polling its own cancellation must not be punished for it.
        """
        run_id = str(run_id)
        with self._lock:
            if self._closed:
                raise TooManyStops(
                    "This gateway is shutting down and is not starting new cancellations.",
                    "The run will be dealt with when it comes back.")
            standing = self._stops.get(run_id)
            if standing is not None and standing.in_flight:
                return standing                                  # join it; costs nothing
            self._within_budget(by)
            stop = Stop(run_id, by, self._clock())
            if standing is not None:
                # A previous attempt that failed. Asking again is a retry, not a duplicate, and
                # what it already tried is carried forward so "attempts" stays true.
                stop.attempts = standing.attempts
            self._stops[run_id] = stop
            self._remember(run_id, stop)
            self._make_sure_there_are_hands()
        try:
            self._work.put_nowait(run_id)
        except queue.Full:
            with self._lock:
                stop.state = GAVE_UP
                stop.problem = "this gateway is already stopping as many runs as it can at once"
                self._forget(run_id)
            raise TooManyStops(
                "This sandbox is already stopping as many runs as it can at once.",
                "The run is still running. Ask again shortly.")
        return stop

    def about(self, run_id: str):
        with self._lock:
            return self._stops.get(str(run_id))

    def in_flight(self, run_id: str) -> bool:
        stop = self.about(run_id)
        return bool(stop is not None and stop.in_flight)

    def busy(self) -> int:
        with self._lock:
            return sum(1 for s in self._stops.values() if s.in_flight)

    # ------------------------------------------------------------------ limits

    def _within_budget(self, by: str) -> None:
        """Called holding the lock. A new stop costs; joining one does not."""
        now = self._clock()
        seen = [t for t in self._recent.get(by, ()) if now - t < self.WINDOW_SECONDS]
        if len(seen) >= self.PER_DEVICE:
            self._recent[by] = seen
            raise TooManyStops(
                "This device has asked for too many cancellations in a short time.",
                "Cancellations already under way are not affected and will finish. Wait a "
                "moment before starting another.")
        seen.append(now)
        self._recent[by] = seen
        # Devices that have gone quiet stop costing memory. Done here rather than on a timer,
        # because a timer is another thing to own.
        for who in [w for w, times in self._recent.items()
                    if not times or now - times[-1] > self.WINDOW_SECONDS * 4]:
            if who != by:
                self._recent.pop(who, None)

    # ------------------------------------------------------------------ the hands

    def _make_sure_there_are_hands(self) -> None:
        """Called holding the lock. Nothing is started until there is something to do."""
        while len(self._hands) < self._hands_wanted:
            hand = threading.Thread(target=self._work_through, daemon=True,
                                    name="agentnode-stopping-%d" % (len(self._hands) + 1))
            self._hands.append(hand)
            hand.start()

    def _work_through(self) -> None:
        while True:
            run_id = self._work.get()
            try:
                if run_id is None:                               # the signal to finish
                    return
                self._carry_one_out(run_id)
            finally:
                self._work.task_done()

    def _carry_one_out(self, run_id: str) -> None:
        with self._lock:
            stop = self._stops.get(run_id)
            if stop is None or stop.state != QUEUED:
                return
            stop.state = WORKING
            stop.attempts += 1
        settled, problem = False, ""
        try:
            settled = bool(self._carry_out(run_id))
        except Exception as exc:                                  # noqa: BLE001
            # A cancellation that failed must not look like one that worked. The run keeps
            # whatever state the gateway gave it, and `status` goes on saying what is true.
            problem = str(exc) or exc.__class__.__name__
        with self._lock:
            stop.settled = settled if not problem else None
            stop.problem = problem
            stop.state = SETTLED if settled else GAVE_UP
            if settled:
                # Forgotten only once the sandbox is confirmed gone. An unsettled stop stays in
                # the journal so a restart picks it up rather than leaving a container behind.
                self._forget(run_id)

    # ------------------------------------------------------------------ the journal

    def _read_journal(self) -> dict:
        try:
            with open(self._journal, encoding="utf-8") as fh:
                kept = json.load(fh)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            # Unreadable is not empty. Saying "nothing was being stopped" because the file could
            # not be parsed is how a container gets left running with nobody accounting for it.
            raise
        return kept if isinstance(kept, dict) else {}

    def _write_journal(self, kept: dict) -> None:
        near = self._journal + ".new"
        with open(near, "w", encoding="utf-8") as fh:
            json.dump(kept, fh, sort_keys=True)
        os.replace(near, self._journal)

    def _remember(self, run_id: str, stop: Stop) -> None:
        try:
            kept = self._read_journal()
            kept[run_id] = {"asked_at": stop.asked_at, "asked_by": stop.asked_by}
            self._write_journal(kept)
        except OSError:
            # Written down before it is attempted, but a gateway that cannot write its journal
            # still stops the run. Losing durability is worse than losing the cancellation.
            pass

    def _forget(self, run_id: str) -> None:
        try:
            kept = self._read_journal()
            if kept.pop(run_id, None) is not None:
                self._write_journal(kept)
        except (OSError, ValueError):
            pass

    def unfinished(self) -> list:
        """Runs this gateway was stopping when it last stopped being a gateway."""
        try:
            return sorted(self._read_journal())
        except (OSError, ValueError):
            return []

    def pick_up_where_it_left_off(self) -> list:
        """Re-ask for everything the journal still remembers. Returns what was picked up.

        Called once on the way up. A run whose record is long gone still has a container named
        after it, and that container does not disappear because the process did.
        """
        again = []
        for run_id in self.unfinished():
            try:
                self.ask(run_id, by="(this gateway, on the way back up)")
                again.append(run_id)
            except TooManyStops:
                break
        return again

    # ------------------------------------------------------------------ the end

    def close(self, seconds: float = GOODBYE_SECONDS) -> None:
        """Stop the hands and wait for them. Nothing started here outlives this call."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            hands = list(self._hands)
        for _ in hands:
            try:
                self._work.put_nowait(None)
            except queue.Full:
                pass
        deadline = time.monotonic() + seconds
        for hand in hands:
            hand.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            self._hands = [h for h in hands if h.is_alive()]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False
