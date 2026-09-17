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


class JournalUnavailable(Exception):
    """The cancellation journal could not be read or written.

    Distinct from an empty journal, and the distinction is the whole point. Absent means nothing
    was being stopped. Unreadable means this gateway does not KNOW what it was stopping -- and a
    gateway that answers the second question with the first one's answer will start clean, having
    quietly decided that the containers it cannot account for do not exist.
    """


class TooManyStops(Exception):
    """A cancellation was refused because of a limit, not because of the run."""

    def __init__(self, because: str, what_to_do: str = "") -> None:
        super().__init__(because)
        self.because = because
        self.what_to_do = what_to_do


class Stop:
    """One cancellation. Repeated requests for the same run share exactly one of these."""

    __slots__ = ("run_id", "asked_at", "asked_by", "attempts", "state", "settled", "problem",
                 "durable")

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
        #: Whether this stop was written down. False means the gateway could not keep its
        #: journal, the cancellation went ahead anyway, and a restart will not pick it up.
        self.durable = False

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
        self._root = str(root)
        self._journal = os.path.join(str(root), "stopping.json")
        self._clock = clock
        self._hands_wanted = max(1, int(hands))
        self._work: queue.Queue = queue.Queue(maxsize=max(1, int(room)))
        self._lock = threading.RLock()
        self._stops: dict = {}
        self._recent: dict = {}
        self._hands: list = []
        self._closed = False
        #: Hands that were still working when `close()` stopped waiting. Set here as well so
        #: reading it before close() says "none" rather than raising.
        self.left_working: list = []
        #: Why this pool cannot keep a durable record, when it cannot. Empty is the ordinary
        #: case and means the journal is doing its job.
        self.journal_problem = ""

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
        """What is being stopped, or the reason it cannot be said. Never a guess.

        Read through the shared retry, because this journal is read while it is written and on
        Windows a reader that arrives during the rename is refused with `PermissionError` --
        which says nothing whatever about the contents. Retried briefly and then raised: a busy
        file is not an absent one, and the paragraph below is why that distinction is kept.
        """
        from agentnode_sdk.gateway.filelock import read_text_with_retry

        try:
            kept = json.loads(read_text_with_retry(self._journal))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            # Unreadable is not empty. Saying "nothing was being stopped" because the file could
            # not be parsed is how a container gets left running with nobody accounting for it.
            raise
        return kept if isinstance(kept, dict) else {}

    def _write_journal(self, kept: dict) -> None:
        """Replace the journal in one step, from a name nobody else is writing.

        The scratch file used to be one fixed name. Two writers over the same directory -- a
        worker forgetting a settled stop while something else remembers a new one, or the
        abandoned pool of a gateway that has just been restarted over its own state -- both
        opened it, and each `open(..., "w")` truncated what the other had not yet flushed. What
        landed was one writer's bytes with the tail of the other's after them: valid JSON
        followed by rubbish, which is how "Extra data: line 1 column 3" appears in a file that
        only ever had whole documents written to it.

        The replace was always atomic. The scratch file was the part that was not, and a unique
        name is what makes it so -- the replace then makes the swap indivisible for readers.

        What it was not was RELIABLE on Windows, where a rename over a file fails for as long as
        any other handle has the target open. This journal is read while it is written -- that is
        what the test beside this is about -- so the second writer got `PermissionError` and
        reported the journal as corrupted when nothing was wrong with it. The same bounded retry
        every other writer of a gateway file uses, from the same place, so there are not two
        answers to one question.
        """
        from agentnode_sdk.gateway.filelock import replace_with_retry

        near = "%s.%d.%d.new" % (self._journal, os.getpid(), threading.get_ident())
        try:
            with open(near, "w", encoding="utf-8") as fh:
                json.dump(kept, fh, sort_keys=True)
            replace_with_retry(near, self._journal)
        except BaseException:
            # Leaving a scratch file behind would be a slow leak in a directory an operator
            # reads. Nothing is raised from here: the original failure is what matters.
            try:
                os.unlink(near)
            except OSError:
                pass
            raise

    def _remember(self, run_id: str, stop: Stop) -> None:
        """Write the stop down before it is attempted, or say plainly that it could not be.

        The cancellation still goes ahead: a run the operator asked to stop is better stopped
        without a record than left running with one. But it is NOT durable, and the previous
        version said nothing at all -- so a gateway whose state directory had gone read-only went
        on answering cancellations exactly as if the journal were working, and a restart would
        have found an empty file and picked up nothing.

        So three things happen instead of nothing: the stop is marked undurable, the reason is
        kept on the pool where `about()` and the operator can see it, and the gateway is stopped
        for NEW work through the same kill switch an operator uses. Cancelling what is already
        running still works; admitting more does not, because a gateway that cannot keep this
        record cannot promise what it promises.
        """
        try:
            kept = self._read_journal()
            kept[run_id] = {"asked_at": stop.asked_at, "asked_by": stop.asked_by}
            self._write_journal(kept)
            stop.durable = True
        except (OSError, ValueError, JournalUnavailable) as problem:
            stop.durable = False
            self._cannot_keep_a_record("write down a cancellation", problem)

    def _forget(self, run_id: str) -> None:
        """Take a settled stop out of the journal, or say why it is still in there.

        Failing to forget is the harmless direction -- the worst it costs is one redundant stop
        of an already-gone container after a restart. It is still reported, because "the journal
        cannot be written" is one fact however it shows up, and an operator finding out from the
        harmless direction first is better than finding out from the other one.
        """
        try:
            # Under the lock: read-modify-write is not one step, and two hands settling at once
            # would otherwise each write back a picture taken before the other's change.
            with self._lock:
                kept = self._read_journal()
                if kept.pop(run_id, None) is not None:
                    self._write_journal(kept)
        except (OSError, ValueError, JournalUnavailable) as problem:
            self._cannot_keep_a_record("forget a settled cancellation", problem)

    def _cannot_keep_a_record(self, doing: str, problem: Exception) -> None:
        """Record the journal failure and stop this gateway taking new work."""
        self.journal_problem = "could not %s: %s" % (doing, problem)
        try:
            from agentnode_sdk.gateway.allowance import stop_everything

            stop_everything(self._root, self.journal_problem, by="(the cancellation journal)")
        except Exception:                                      # noqa: BLE001
            # The kill switch lives in the same directory that just failed, so it may well fail
            # too. Nothing further can be done from in here, and the reason is on the pool where
            # `about()` reports it either way -- what must not happen is this raising and taking
            # the cancellation down with it.
            pass

    def unfinished(self) -> list:
        """Runs this gateway was stopping when it last stopped being a gateway.

        Raises `JournalUnavailable` when the journal exists and cannot be read. It used to answer
        the empty list, which is the answer to a different question: `_read_journal` was careful
        to distinguish absent from unreadable and this threw that away one frame later.
        """
        try:
            return sorted(self._read_journal())
        except (OSError, ValueError) as problem:
            raise JournalUnavailable(str(problem)) from problem

    def pick_up_where_it_left_off(self) -> list:
        """Re-ask for everything the journal still remembers. Returns what was picked up.

        Called once on the way up. A run whose record is long gone still has a container named
        after it, and that container does not disappear because the process did.
        """
        try:
            picking_up = self.unfinished()
        except JournalUnavailable as problem:
            # Fail closed, the same way an unreadable stop file means stopped. This gateway
            # cannot say what it was tearing down, so it does not come up taking work and
            # pretending it knows; the reason is written where an operator will find it.
            self._cannot_keep_a_record("read the cancellation journal", problem)
            return []
        again = []
        for run_id in picking_up:
            try:
                self.ask(run_id, by="(this gateway, on the way back up)")
                again.append(run_id)
            except TooManyStops:
                break
        return again

    # ------------------------------------------------------------------ the end

    def close(self, seconds: float | None = None) -> list:
        """Stop the hands, wait for them, and SAY which would not end.

        The wait is bounded, so "nothing started here outlives this call" is a claim this cannot
        make on every path and must not pretend to. What it can do is never lose track: a hand
        still alive when the wait runs out is returned by name and kept on `left_working`, so the
        service closing this pool can report it rather than quietly assuming it stopped.
        """
        # None rather than the constant as a default, so that GOODBYE_SECONDS can be changed on
        # an instance and mean something. A default evaluated at class creation cannot be.
        seconds = self.GOODBYE_SECONDS if seconds is None else seconds
        with self._lock:
            if self._closed:
                return list(self.left_working)
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
            self.left_working = [h.name for h in self._hands]
        return list(self.left_working)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False
