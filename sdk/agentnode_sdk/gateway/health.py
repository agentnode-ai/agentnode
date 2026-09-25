"""Whether this gateway may take work RIGHT NOW, as distinct from what it once measured.

The difference is the whole point of this file, and it was found the hard way. The closed alpha
was left with its worker stopped for two minutes. The gateway did the right things -- it stayed
up, refused every job and booked nothing -- but `agentnode-measure`, a oneshot with
`RemainAfterExit=yes`, went on reporting the verdict of its last successful run:

    Protected -- code sent here runs inside a container, as a user with no privileges, and is
    cleaned up afterwards. This has been measured, not assumed.

A machine that could not run a job was reporting that it was protecting one. That is the failure
mode the restart-recovery arc had in view when it asked for visible failure: something broken
that does not look broken. `HEALTH-HONESTY-0001` decided not to fix it by making the gateway die
-- that is worse and less available -- but by making the machine stop claiming health it does not
have. `HEALTH-HONESTY-0002` chose this mechanism and fixed the window.

## What this is, and what it is not

`ReadinessGate` in `readiness.py` decides what a stored measurement PROVES: it binds a report to
a boot, an image, a schema, a topology and a policy, and it expires by age. That stays
authoritative and is not touched here. Worker reachability is deliberately NOT another field in
that binding: boot id and image digest are facts about what was measured, and reachability is a
changing condition. Binding it would need a new value on every disconnect and would confuse a
report's identity with a machine's health.

This decides something else: whether a proof that is valid is currently ELIGIBLE. A worker that
has gone away does not make the old measurement wrong about the past. It makes it irrelevant to
now.

## Three states, and only three

    protected     the worker answered, and a measurement valid for this state has succeeded
    unavailable   the worker did not answer; nothing is admitted
    measuring     the worker answers again, but the new measurement has not finished

`measuring` exists because reachability returning is not evidence that the sandbox still
enforces anything. Between the worker going and coming back it may be a different worker, a
different image, or the same one with less of a ceiling. So coming back opens nothing by itself.

There is a fourth value, `starting`, and it is deliberately not one of the three: it is what is
published before the first probe has returned, and it is the only one of the four that is not an
observation. It admits, and the reason it may is worth being explicit about, because it is the
one place here that does not block. A gateway that has just started has observed nothing wrong;
what protects that window is the per-job reachability check that predates all of this
(`server.py`, before a run id is claimed), and the window is bounded by the first probe -- at
most one interval. `starting` is never rendered as `protected`, and it can never be returned to:
every path out of it is an observation. A gateway whose watch is never started -- an in-process
worker, a test -- stays in it, which is exactly the behaviour those had before this file existed.

## The window

At most fifteen seconds from the worker actually becoming unreachable to `unavailable` being
published: a probe starts no more than ten seconds after the previous scheduled boundary, and
each probe has a hard five-second deadline. Scheduling is on a monotonic clock, because a clock
that can be set backwards -- and this deployment sets it backwards on purpose to test its floors
-- must not be able to stretch the window.

The probe is a real authenticated round trip. An open TCP port is not enough: a port that
accepts and then fails the handshake is exactly the state this exists to catch.
"""
from __future__ import annotations

import json
import os
import pathlib
import threading
import time
from dataclasses import dataclass

#: How long after the previous scheduled boundary the next probe may start.
PROBE_EVERY_SECONDS = 10.0

#: And how long one probe may take before it counts as a failure. Connection, mutual-TLS
#: identity acceptance and the worker's answer all have to fit inside it.
PROBE_DEADLINE_SECONDS = 5.0

#: The promise: the most that may elapse between the worker becoming unreachable and this
#: saying so. It is the sum of the two above, and it is stated as its own constant because it is
#: what the machine is judged against rather than an accident of how the other two are set.
MAX_DETECTION_SECONDS = PROBE_EVERY_SECONDS + PROBE_DEADLINE_SECONDS

PROTECTED = "protected"
UNAVAILABLE = "unavailable"
MEASURING = "measuring"
#: Before the first probe. See the module docstring: not an observation, and not `protected`.
STARTING = "starting"

#: Stable codes, so that a caller can tell the three apart without reading a sentence. The
#: friendly sentence stays alongside; what was wrong before was that ONLY the sentence existed,
#: and it was the same sentence for every cause.
WORKER_UNREACHABLE = "worker_unreachable"
MEASUREMENT_RUNNING = "measurement_running"
MEASUREMENT_FAILED = "measurement_failed"
NOT_YET_PROBED = "not_yet_probed"
OK = "ok"


@dataclass(frozen=True)
class Health:
    """One atomic answer. Never assembled from two reads that could disagree."""

    state: str
    code: str
    reason: str
    #: Bumped on every observed loss. A measurement that started under an older generation may
    #: not publish `protected`, because the worker it measured may not be the worker that is
    #: there now.
    generation: int = 0
    #: Wall clock, for a reader. Never used to decide anything -- see the monotonic note above.
    at: float = 0.0
    #: Monotonic, and what the window is actually measured against.
    since: float = 0.0

    @property
    def may_admit(self) -> bool:
        """`starting` is here for the reason the module docstring gives, and for no other."""
        return self.state in (PROTECTED, STARTING)

    @property
    def summary(self) -> str:
        """The same answer with nothing in it that describes this machine.

        `reason` carries the exception the probe saw, and that names the worker's address and
        the errno. Those belong to an operator: `gateway status`, `gateway watch`, and the
        statement in the gateway's own 0700 state directory. They do NOT belong on a door that
        anybody can reach without a credential, and `/v1/health` and `/v1/hello` are both such
        doors -- `test_two_accounts_every_door` and `TestHealthGivesNothingAway` are there
        because somebody already thought about this, and the first version of this change walked
        straight past them and put `tcps://127.0.0.1:8443` on both.

        So everything that crosses a network door says one of these instead, and each is a fixed
        phrase rather than anything composed from what went wrong.
        """
        return {
            PROTECTED: "this sandbox is taking work.",
            MEASURING: "this sandbox is establishing what it can enforce and is not taking work "
                       "yet.",
            STARTING: "this sandbox has not finished starting.",
        }.get(self.state,
              "this sandbox is not taking work: it cannot currently reach what runs code.")

    @property
    def observed(self) -> bool:
        """Whether a probe has ever returned. False only in `starting`."""
        return self.state != STARTING

    def as_dict(self) -> dict:
        return {"state": self.state, "code": self.code, "reason": self.reason,
                "generation": self.generation, "at": self.at}


#: What is published before the first probe has finished. Not `protected`, and not claimed as
#: an observation: a gateway that has not yet asked does not know.
def starting() -> Health:
    return Health(STARTING, NOT_YET_PROBED,
                  "this gateway has not yet asked its worker whether it is there. Each job "
                  "still reaches the worker before anything is claimed.", 0, 0.0, 0.0)


#: How long a published statement may go unrefreshed before a reader stops believing a
#: PERMISSIVE one. Three turns of the loop: one missed turn is a slow machine, three is a
#: gateway that is not running.
STALE_AFTER_SECONDS = 3 * MAX_DETECTION_SECONDS

HEALTH_FILE = "health.json"
NO_STATEMENT = "no_statement"
STALE = "stale_statement"


def read_published(path, now: float | None = None) -> Health:
    """What the gateway last said about itself, aged, for a reader in another process.

    The ageing is ASYMMETRIC, and that asymmetry is the whole lesson of this arc. A statement
    that has stopped being refreshed may never go on permitting anything: an old `protected`
    reads as a refusal, because a gateway that is not writing is a gateway that is not probing,
    and what it last saw is not what is true now. A statement that BLOCKS is carried forward
    unchanged however old it is -- there is nothing unsafe about continuing to refuse, and
    downgrading a specific `unavailable` to a vague one would lose the reason.

    That is exactly what the installed `agentnode-measure` oneshot did wrong. It held the verdict
    of its last successful run for as long as it was loaded, and the verdict it held was the
    permissive one.
    """
    at = time.time() if now is None else now
    try:
        said = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        if not isinstance(said, dict):
            raise ValueError("not an object")
    except FileNotFoundError:
        # NOTHING WAS EVER SAID, which is not the same as something unreadable. No gateway has
        # served from this directory in a build that can speak, so there is no live statement
        # to carry forward and nothing has been observed about a worker. Reported as `starting`
        # -- no opinion -- and NOT as a refusal: turning "no gateway is running here" into
        # "the worker is down" would be inventing an observation nobody made.
        return Health(STARTING, NO_STATEMENT,
                      "this gateway has said nothing about its own health; either nothing is "
                      "serving from here, or this is a build from before it could say.",
                      0, 0.0, 0.0)
    except (OSError, ValueError) as unreadable:
        # SOMETHING IS THERE AND CANNOT BE READ. Deliberately not folded in with the case above:
        # absent is not unreadable. A file that exists and does not parse means a writer is
        # producing something this reader does not understand, or the directory has been
        # tampered with, and neither is a reason to permit anything.
        return Health(UNAVAILABLE, STALE,
                      "this gateway's health statement is there and cannot be read (%s), so it "
                      "is not being taken as permission." % (unreadable,), 0, 0.0, 0.0)

    health = Health(str(said.get("state") or ""), str(said.get("code") or ""),
                    str(said.get("reason") or ""), int(said.get("generation") or 0),
                    float(said.get("at") or 0.0), 0.0)
    if health.state not in (PROTECTED, UNAVAILABLE, MEASURING, STARTING):
        return Health(UNAVAILABLE, STALE,
                      "this gateway said something about its health that this build does not "
                      "understand, so it is not being taken as permission.", health.generation,
                      health.at, 0.0)
    if not health.may_admit:
        return health                                         # already a refusal; keep its reason

    allowed = float(said.get("window_seconds") or MAX_DETECTION_SECONDS) * 3
    age = at - health.at
    if health.at <= 0.0 or age > allowed:
        return Health(
            UNAVAILABLE, STALE,
            "this gateway last said it was %s %s, and it has not said anything since. A "
            "statement that is not being refreshed is not a current one, so it is not being "
            "taken as permission." % (health.state, _ago(age)),
            health.generation, health.at, 0.0)
    return health


def _ago(seconds: float) -> str:
    if seconds < 0:
        # The file is dated in the future. A clock was moved, and that is worth saying plainly
        # rather than rendering as a negative number of seconds ago.
        return "at a time later than now, which means a clock here was changed"
    if seconds < 90:
        return "%d seconds ago" % int(seconds)
    return "%d minutes ago" % int(seconds // 60)


@dataclass
class _Probe:
    """One attempt, and what it cost. Kept so the window can be measured rather than assumed."""

    began: float
    ended: float = 0.0
    reached: bool = False
    said: str = ""
    deadline: float = PROBE_DEADLINE_SECONDS

    @property
    def seconds(self) -> float:
        return max(0.0, self.ended - self.began)

    @property
    def overran(self) -> bool:
        return self.seconds > self.deadline


class HealthWatch:
    """Asks the worker, on a schedule, and publishes one state.

    It lives inside the gateway rather than beside it so that health and admission cannot be two
    authorities racing each other: the same object that notices the loss is the one the
    admission path reads.
    """

    def __init__(self, reach, measure, *, every: float = PROBE_EVERY_SECONDS,
                 deadline: float = PROBE_DEADLINE_SECONDS,
                 clock=time.monotonic, wall=time.time, say=None,
                 publish_to: "os.PathLike[str] | str | None" = None) -> None:
        #: Called with the deadline in seconds, to establish that the worker answers. Must raise
        #: to mean "it did not". The deadline is passed rather than wrapped in a timer because a
        #: timer around a blocking socket does not end the wait, it only stops watching it.
        self._reach = reach
        #: Called to take a fresh measurement. Returns something truthy for success.
        self._measure = measure
        self._every = every
        self._deadline = deadline
        self._clock = clock
        self._wall = wall
        self._say = say or (lambda _line: None)
        self._lock = threading.Lock()
        self._health = starting()
        self._generation = 0
        self._probes: list = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        #: Held for the whole of a measurement, so two cannot run at once and a stale one cannot
        #: finish on top of a newer one.
        self._measuring = threading.Lock()
        #: Where the operator-visible copy goes. The object in this process stays authoritative
        #: for admission -- nothing is admitted on the strength of a file -- and this is what
        #: `gateway status`, `gateway watch` and whatever operations runs read, because they run
        #: in a DIFFERENT process from the gateway and cannot see the object at all. The same
        #: split as `conformance.json` beside the activation snapshot, for the same reason.
        self._publish_to = None if publish_to is None else pathlib.Path(publish_to)

    # ------------------------------------------------------------------ what it publishes

    def now(self) -> Health:
        with self._lock:
            return self._health

    def probes(self) -> list:
        with self._lock:
            return list(self._probes[-20:])

    def _publish(self, state: str, code: str, reason: str) -> Health:
        with self._lock:
            health = Health(state, code, reason, self._generation, self._wall(), self._clock())
            self._health = health
        self._write_out(health)
        return health

    def _write_out(self, health: Health) -> None:
        """The operator-visible copy, replaced atomically or not at all.

        A reader must never see half a state. Written to a neighbouring name and renamed, which
        on every platform this gateway serves on replaces the file in one step.

        Failures here are deliberately swallowed: a gateway that cannot write a diagnostic file
        is still a gateway that knows its own health, and turning that into a crash would make
        an unwritable directory into an outage. What it must not do is leave the OLD file behind
        looking current -- and it does not, because a reader ages the file (`read_published`).
        """
        if self._publish_to is None:
            return
        said = dict(health.as_dict())
        #: What a reader needs to age this file without trusting a clock it does not share.
        said["window_seconds"] = self._every + self._deadline
        said["pid"] = os.getpid()
        beside = self._publish_to.with_name(self._publish_to.name + ".writing.%d" % os.getpid())
        try:
            self._publish_to.parent.mkdir(parents=True, exist_ok=True)
            beside.write_text(json.dumps(said, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(beside, self._publish_to)
        except OSError:
            try:
                beside.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------ one turn of the loop

    def probe_once(self) -> _Probe:
        """Ask the worker, with a deadline. Never raises."""
        attempt = _Probe(began=self._clock(), deadline=self._deadline)
        try:
            self._reach(self._deadline)
            attempt.reached = True
        except BaseException as refused:                      # noqa: BLE001 - any failure is one
            attempt.said = type(refused).__name__ + ": " + str(refused)[:200]
        attempt.ended = self._clock()
        # A probe that answered but took longer than it was given is a failure. Otherwise a
        # worker that answers in a minute would keep the machine looking healthy for a minute.
        if attempt.reached and attempt.overran:
            attempt.reached = False
            attempt.said = ("the worker answered, but after %.1fs, which is longer than the %.1fs "
                            "a probe is given" % (attempt.seconds, attempt.deadline))
        with self._lock:
            self._probes.append(attempt)
            del self._probes[:-40]
        return attempt

    def consider(self, attempt: _Probe) -> Health:
        """What one probe means for the state. This is the whole state machine."""
        was = self.now()

        if not attempt.reached:
            if was.state != UNAVAILABLE or was.code != WORKER_UNREACHABLE:
                # A loss. Everything measured before it stops being eligible, and anything
                # already measuring is now measuring a worker that may not be there.
                with self._lock:
                    self._generation += 1
                self._say("the worker stopped answering: " + attempt.said)
            return self._publish(
                UNAVAILABLE, WORKER_UNREACHABLE,
                "the sandbox worker is not answering, so this gateway cannot run anything and "
                "will not pretend it can. " + attempt.said)

        if was.state == PROTECTED:
            # Still fine. Republished so that `at` moves and a reader can tell a live answer
            # from one that stopped being updated.
            return self._publish(PROTECTED, OK, was.reason)

        if not was.observed:
            # FIRST PROBE, and it answered. No loss has been observed, so there is nothing for a
            # re-measurement to establish that is not already established: the stored report is
            # bound to this boot, this image, this topology and this policy, and `ReadinessGate`
            # rejects it otherwise. Forcing a fresh measurement here would take the machine out
            # of service for a minute or two after every restart and buy nothing, and the gate
            # that decides whether the report is eligible still runs on every admission.
            return self._publish(
                PROTECTED, OK,
                "the worker is answering, and what was measured about it still describes this "
                "boot and this image.")

        # It answers again AFTER A LOSS. That is not permission: between going and coming back
        # it may be a different worker, a different image, or the same one with less of a
        # ceiling, and none of that would show in a report bound before it went.
        return self._publish(
            MEASURING, MEASUREMENT_RUNNING,
            "the worker is answering again, and this gateway is measuring what it can enforce "
            "before it runs anything. Nothing is admitted until that finishes.")

    def remeasure_if_needed(self) -> Health:
        """Take a fresh measurement when the state calls for one, and only publish on success.

        Serialized, and checked against the generation twice: once before, once after. A
        measurement that began before a loss may not decide anything about after it.
        """
        health = self.now()
        if health.state != MEASURING:
            return health
        if not self._measuring.acquire(blocking=False):
            return health                                     # one is already running
        try:
            began_under = self.now().generation
            try:
                measured = self._measure()
            except BaseException as failed:                   # noqa: BLE001
                return self._publish(
                    UNAVAILABLE, MEASUREMENT_FAILED,
                    "the worker is answering, but measuring what it enforces did not finish: "
                    + type(failed).__name__ + ": " + str(failed)[:200])
            if self.now().generation != began_under:
                # The worker went away while this was running. What it measured is about a
                # machine that no longer exists.
                return self.now()
            if not measured:
                return self._publish(
                    UNAVAILABLE, MEASUREMENT_FAILED,
                    "the worker is answering, but the measurement did not establish what this "
                    "gateway needs to enforce, so nothing will be run.")
            # One last authenticated round trip, so that `protected` is never published about a
            # worker that vanished between the measurement finishing and this line.
            final = self.probe_once()
            if not final.reached or self.now().generation != began_under:
                return self.consider(final)
            return self._publish(
                PROTECTED, OK,
                "the worker is answering and a fresh measurement of what it enforces has "
                "succeeded since it came back.")
        finally:
            self._measuring.release()

    def turn(self) -> Health:
        """One probe, what it means, and any measurement it calls for."""
        self.consider(self.probe_once())
        return self.remeasure_if_needed()

    # ------------------------------------------------------------------ lifecycle

    def what_it_still_owes(self) -> Health:
        """The state a starting watch should begin in, given what this gateway last wrote.

        The re-measurement gate is tied to an OBSERVED loss, and a loss is observed by a process.
        So a gateway that restarts while its worker is away would otherwise come back with no
        memory of it: first probe succeeds, `protected`, no fresh measurement. The binding
        catches a worker that CHANGED in the gap -- image, configuration, runtime version, boot
        -- but not the same one whose ceilings quietly stopped binding, which is the case the
        gate exists for.

        So the statement this gateway wrote before it stopped is read back, and a loss it had not
        finished answering is carried across the restart as one still to answer. It costs one
        measurement on a gateway that went down unwell, and nothing on one that did not. It is
        not aged: an old unanswered loss is still unanswered -- and a `protected` statement that
        has gone stale reads as a refusal anyway, so a gateway that was down long enough for that
        also re-measures, which is the safe direction.

        Separate from `start` so that it can be asked without a thread running, which is the only
        way a test can see the decision rather than whatever the first turn has already done to
        it.
        """
        if self._publish_to is None:
            return starting()
        previous = read_published(self._publish_to, self._wall())
        if previous.state in (UNAVAILABLE, MEASURING) and previous.code != NO_STATEMENT:
            return Health(
                MEASURING, MEASUREMENT_RUNNING,
                "this gateway last recorded that it could not run anything, so it is measuring "
                "what it can enforce before it takes work again.")
        return starting()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        began = self.what_it_still_owes()
        if began.state != STARTING:
            # An unanswered loss carried across the restart counts as one, so a measurement that
            # was already running somewhere cannot come back and settle it.
            with self._lock:
                self._generation += 1
        # So the statement exists from the moment the gateway serves. Without it a reader in
        # another process cannot tell a gateway that started a second ago from one that is not
        # running, and would report the second thing about the first. Published through the
        # ordinary path rather than written out directly, so it carries a real timestamp and
        # ages like everything else: a `starting` that is never followed by a probe is a watch
        # that is not working, and that must stop being believed like any other stale statement.
        self._publish(began.state, began.code, began.reason)
        self._thread = threading.Thread(target=self._loop, name="health-watch", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            began = self._clock()
            try:
                self.turn()
            except BaseException as broke:                    # noqa: BLE001 - never die quietly
                self._say("the health watch itself failed: %r" % (broke,))
                self._publish(UNAVAILABLE, WORKER_UNREACHABLE,
                              "this gateway cannot establish whether its worker is reachable: "
                              "%r. Nothing will be run until it can." % (broke,))
            # From the BOUNDARY, not from when the work finished, so a slow probe does not push
            # the next one out and stretch the window past what was promised.
            waiting = self._every - (self._clock() - began)
            self._stop.wait(max(0.0, waiting))

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=PROBE_DEADLINE_SECONDS + self._every + 5.0)
