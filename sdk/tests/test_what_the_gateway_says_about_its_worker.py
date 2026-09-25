"""What a machine says about itself while its worker is gone.

The closed alpha was left with its worker stopped for two minutes. The gateway did the right
things -- it stayed up, refused every job and booked nothing -- and `agentnode-measure`, a
oneshot with `RemainAfterExit=yes`, went on saying:

    Protected -- code sent here runs inside a container, as a user with no privileges, and is
    cleaned up afterwards. This has been measured, not assumed.

Every test here is about that sentence, and about the three states `HEALTH-HONESTY-0001` requires
the machine to distinguish: `protected`, `unavailable` and `measuring`.

None of them reads the source to see whether a thing exists. Each one drives the state machine or
the published statement and asserts what came out, because "it is implemented" and "it does it"
are different claims and only the second one was ever in question.
"""
from __future__ import annotations

import json

import pytest

from agentnode_sdk.gateway import health as H
from agentnode_sdk.worker import WorkerUnreachable


class _Clock:
    """A monotonic clock a test can move, so a window can be measured rather than waited out."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def tick(self, seconds: float) -> None:
        self.t += seconds


def _watch(reach, measure=lambda: True, **kw):
    clock = _Clock()
    watch = H.HealthWatch(reach, measure, clock=clock, wall=lambda: clock.t, **kw)
    return watch, clock


def _there(_budget):
    return None


def _gone(_budget):
    raise WorkerUnreachable("the sandbox worker at tcps://127.0.0.1:8443 could not be reached")


# ------------------------------------------------------------------ the three states


def test_a_worker_that_does_not_answer_makes_the_gateway_say_unavailable_and_not_protected():
    watch, _clock = _watch(_there)
    watch.turn()
    assert watch.now().state == H.PROTECTED

    watch._reach = _gone
    said = watch.turn()

    assert said.state == H.UNAVAILABLE
    assert said.code == H.WORKER_UNREACHABLE
    assert not said.may_admit
    # The point of the whole arc: not merely "not protected" internally, but nothing anywhere
    # still carrying the word.
    assert "protected" not in said.reason.lower()


def test_a_worker_that_comes_back_does_not_restore_protected_by_itself():
    """Reachability returning is not evidence that the sandbox still enforces anything."""
    measured: list = []

    def measure():
        measured.append(True)
        return True

    watch, _clock = _watch(_there, measure)
    watch.turn()
    watch._reach = _gone
    watch.turn()
    assert watch.now().state == H.UNAVAILABLE

    # It answers again -- and that alone must not open anything. `consider` is the half of a
    # turn that sees the probe; nothing has been measured yet at this point.
    watch._reach = _there
    watch.consider(watch.probe_once())

    assert watch.now().state == H.MEASURING
    assert watch.now().code == H.MEASUREMENT_RUNNING
    assert not watch.now().may_admit
    # And it is `measuring` because the measurement has NOT run yet, rather than because one
    # ran and said nothing useful.
    assert measured == []


def test_protected_comes_back_only_after_the_new_measurement_succeeds():
    watch, _clock = _watch(_there)
    watch.turn()
    watch._reach = _gone
    watch.turn()
    watch._reach = _there

    back = watch.turn()

    assert back.state == H.PROTECTED
    assert back.code == H.OK
    assert back.may_admit


def test_a_measurement_that_fails_leaves_the_machine_unavailable_and_says_which():
    watch, _clock = _watch(_there, measure=lambda: False)
    watch.turn()
    watch._reach = _gone
    watch.turn()                                              # a loss, so a re-measurement is due
    watch._reach = _there
    watch.turn()                                              # -> MEASURING -> the measurement fails

    assert watch.now().state == H.UNAVAILABLE
    assert watch.now().code == H.MEASUREMENT_FAILED
    # Distinguishable from an unreachable worker WITHOUT reading the sentence, which is what
    # `H4` asks for and what one generic sentence for every cause could not give.
    assert watch.now().code != H.WORKER_UNREACHABLE


def test_a_measurement_that_raises_is_a_refusal_and_not_a_crash():
    def measure():
        raise RuntimeError("the runner could not start")

    watch, _clock = _watch(_there, measure)
    watch.turn()
    watch._reach = _gone
    watch.turn()
    watch._reach = _there
    watch.turn()

    assert watch.now().state == H.UNAVAILABLE
    assert watch.now().code == H.MEASUREMENT_FAILED
    assert "the runner could not start" in watch.now().reason


# ------------------------------------------------------------------ the window


def test_the_window_it_promises_is_the_window_it_is_built_from():
    """Not a number in a document. The two parts the schedule is actually made of."""
    assert H.MAX_DETECTION_SECONDS == H.PROBE_EVERY_SECONDS + H.PROBE_DEADLINE_SECONDS
    assert H.MAX_DETECTION_SECONDS <= 15.0


def test_a_worker_that_accepts_and_then_stalls_counts_as_not_answering():
    """An open port is not an answer, and neither is a slow one.

    This is the state the whole mechanism exists to catch: something accepting connections while
    being unable to do anything. A probe that eventually succeeds after longer than it was given
    would otherwise keep the machine looking healthy for as long as the stall lasted.
    """
    clock = _Clock()

    def slow(_budget):
        clock.tick(9.0)                                       # longer than the 5s it was given

    watch = H.HealthWatch(slow, lambda: True, clock=clock, wall=lambda: clock.t)
    watch.turn()

    assert watch.now().state == H.UNAVAILABLE
    assert watch.now().code == H.WORKER_UNREACHABLE
    assert "longer than" in watch.now().reason


def test_the_probe_is_given_the_deadline_rather_than_left_to_the_transport():
    given: list = []

    def remember(budget):
        given.append(budget)

    watch, _clock = _watch(remember)
    watch.probe_once()

    assert given == [H.PROBE_DEADLINE_SECONDS]


def test_a_slow_turn_does_not_push_the_next_probe_out_past_the_window():
    """The wait is from the scheduled boundary, not from when the work happened to finish."""
    clock = _Clock()
    watch = H.HealthWatch(_there, lambda: True, clock=clock, wall=lambda: clock.t)
    watch.turn()                                              # settle into `protected` first

    def slow(_budget):
        clock.tick(4.0)                                       # inside the 5s deadline, but slow

    watch._reach = slow
    boundary = clock.t
    watch.turn()
    # What `_loop` waits: the interval minus what the turn already spent, so the NEXT probe
    # starts one interval after this one did rather than one interval after it finished.
    waiting = watch._every - (clock.t - boundary)

    assert waiting == pytest.approx(6.0)
    assert waiting + (clock.t - boundary) == pytest.approx(H.PROBE_EVERY_SECONDS)


# ------------------------------------------------------------------ a worker replaced underneath


def test_a_measurement_that_began_before_a_loss_cannot_publish_protected():
    """The worker it measured may not be the worker that is there now."""
    clock = _Clock()
    state = {"reachable": True}

    def reach(_budget):
        if not state["reachable"]:
            raise WorkerUnreachable("gone")

    def measure():
        # The worker disappears WHILE this is running, exactly as it could on a real machine.
        state["reachable"] = False
        watch.consider(watch.probe_once())
        return True

    watch = H.HealthWatch(reach, measure, clock=clock, wall=lambda: clock.t)
    watch.turn()                                              # settle into `protected`
    state["reachable"] = False
    watch.turn()                                              # a loss
    state["reachable"] = True
    watch.consider(watch.probe_once())                        # -> MEASURING
    watch.remeasure_if_needed()

    assert watch.now().state == H.UNAVAILABLE
    assert watch.now().state != H.PROTECTED


def test_every_observed_loss_moves_the_generation_on():
    watch, _clock = _watch(_there)
    watch.turn()
    first = watch.now().generation

    watch._reach = _gone
    watch.turn()
    after_one_loss = watch.now().generation
    watch.turn()                                              # still gone; not a NEW loss
    still = watch.now().generation

    assert after_one_loss == first + 1
    assert still == after_one_loss


# ------------------------------------------------------------------ what another process reads


def test_a_reader_in_another_process_sees_the_state_the_gateway_published(tmp_path):
    where = tmp_path / H.HEALTH_FILE
    watch, clock = _watch(_gone, publish_to=where)
    watch.turn()

    read = H.read_published(where, now=clock.t)

    assert read.state == H.UNAVAILABLE
    assert read.code == H.WORKER_UNREACHABLE
    assert not read.may_admit


def test_a_protected_statement_that_stopped_being_refreshed_stops_being_believed(tmp_path):
    """The exact defect: a permissive verdict carried forward after it stopped being true."""
    where = tmp_path / H.HEALTH_FILE
    watch, clock = _watch(_there, publish_to=where)
    watch.turn()
    assert H.read_published(where, now=clock.t).state == H.PROTECTED

    # The gateway goes away. Nothing rewrites the file; it simply stops being refreshed.
    read = H.read_published(where, now=clock.t + H.STALE_AFTER_SECONDS + 1.0)

    assert read.state == H.UNAVAILABLE
    assert read.code == H.STALE
    assert not read.may_admit


def test_a_refusal_is_carried_forward_however_old_it_is(tmp_path):
    """Ageing is asymmetric on purpose: continuing to refuse is never the unsafe direction."""
    where = tmp_path / H.HEALTH_FILE
    watch, clock = _watch(_gone, publish_to=where)
    watch.turn()

    read = H.read_published(where, now=clock.t + 10 * H.STALE_AFTER_SECONDS)

    assert read.state == H.UNAVAILABLE
    assert read.code == H.WORKER_UNREACHABLE                  # the reason survives, not just "no"


def test_nothing_published_is_not_the_same_as_something_unreadable(tmp_path):
    absent = H.read_published(tmp_path / "nothing-here.json")
    assert absent.state == H.STARTING
    assert absent.code == H.NO_STATEMENT

    corrupt = tmp_path / H.HEALTH_FILE
    corrupt.write_text("{ this is not json", encoding="utf-8")
    unreadable = H.read_published(corrupt)
    assert unreadable.state == H.UNAVAILABLE
    assert not unreadable.may_admit


def test_a_state_this_build_does_not_understand_is_not_taken_as_permission(tmp_path):
    where = tmp_path / H.HEALTH_FILE
    where.write_text(json.dumps({"state": "fine", "code": "ok", "at": 10 ** 12}),
                     encoding="utf-8")

    read = H.read_published(where)

    assert read.state == H.UNAVAILABLE
    assert not read.may_admit


def test_the_statement_is_replaced_whole_and_never_left_half_written(tmp_path):
    where = tmp_path / H.HEALTH_FILE
    watch, _clock = _watch(_there, publish_to=where)
    watch.start()
    try:
        for _ in range(5):
            watch.turn()
    finally:
        watch.stop()

    assert json.loads(where.read_text(encoding="utf-8"))["state"] in (
        H.PROTECTED, H.MEASURING, H.STARTING)
    # Nothing left behind from the write-then-rename.
    assert [p.name for p in tmp_path.iterdir()] == [H.HEALTH_FILE]


# ------------------------------------------------------------------ the starting state


def test_before_the_first_probe_the_machine_does_not_claim_to_be_protected():
    watch, _clock = _watch(_there)

    assert watch.now().state == H.STARTING
    assert watch.now().state != H.PROTECTED
    assert not watch.now().observed


def test_starting_cannot_be_returned_to_once_anything_has_been_observed():
    watch, _clock = _watch(_gone)
    watch.turn()
    watch._reach = _there
    watch.turn()
    watch._reach = _gone
    watch.turn()

    assert watch.now().state == H.UNAVAILABLE
    assert watch.now().observed


def test_a_first_probe_that_answers_does_not_force_a_re_measurement():
    """No loss has been observed, so there is nothing a fresh measurement would establish.

    The stored report is bound to this boot, this image, this topology and this policy, and
    `ReadinessGate` rejects it when any of those changed. Measuring again at every restart would
    take the machine out of service for a minute or two and buy nothing.
    """
    measured: list = []
    watch, _clock = _watch(_there, lambda: measured.append(True) or True)

    watch.turn()

    assert watch.now().state == H.PROTECTED
    assert measured == []


# ------------------------------------------------------------------ across the gateway's own restart


def test_a_loss_it_had_not_answered_survives_the_gateway_restarting(tmp_path):
    """The gate is tied to an OBSERVED loss, and a loss is observed by a process.

    Without this, a gateway that restarted while its worker was away came back with no memory of
    it: first probe succeeds, `protected`, no fresh measurement. The binding catches a worker
    that CHANGED in the gap; it does not catch the same one whose ceilings quietly stopped
    binding, which is the case the gate exists for.
    """
    where = tmp_path / H.HEALTH_FILE
    first, clock = _watch(_gone, publish_to=where)
    first.turn()
    assert H.read_published(where, now=clock.t).state == H.UNAVAILABLE

    # A NEW watch, as a restarted gateway has -- and its worker is answering again. Asked
    # WITHOUT starting the thread, so what is seen is the decision and not whatever a first turn
    # has already done to it.
    second, _clock = _watch(_there, publish_to=where)
    owed = second.what_it_still_owes()

    assert owed.state == H.MEASURING, (
        "a restarted gateway must not skip the measurement its predecessor still owed")
    assert not owed.may_admit


def test_a_healthy_statement_it_just_wrote_is_not_a_loss_to_answer(tmp_path):
    """The counter-check for the one above: it has to be able to NOT fire.

    Otherwise every restart would re-measure for a reason nobody established, and "it measures
    again" would stop meaning "something was wrong". It still does not ADMIT -- a started watch
    never does before its first probe -- so what this distinguishes is `unavailable` from
    `measuring`, which is the difference between "ask the worker" and "measure it all again".
    """
    where = tmp_path / H.HEALTH_FILE
    first, clock = _watch(_there, publish_to=where)
    first.turn()
    assert H.read_published(where, now=clock.t).state == H.PROTECTED

    second = H.HealthWatch(_there, lambda: True, publish_to=where,
                           clock=lambda: clock.t, wall=lambda: clock.t)

    owed = second.what_it_still_owes()
    assert owed.state == H.UNAVAILABLE and owed.code == H.NOT_YET_PROBED
    assert owed.state != H.MEASURING, "there was no loss to answer, so nothing is owed"


def test_a_started_watch_does_not_admit_before_its_first_probe(tmp_path):
    """A gateway that has not asked its worker anything does not know, and not knowing is not
    permission.

    The first version of this began in `starting`, which admits, so a gateway that came up with
    its worker ALREADY unreachable would take work for up to one probe on the strength of a
    stored measurement about a machine that was not answering. `MTLS-DEFAULT-R2-0001` found it
    on H3: absent exercise is not a pass, and the state that made it possible was in the source.
    """
    watch, _clock = _watch(_gone, publish_to=tmp_path / H.HEALTH_FILE)

    assert watch.now().state == H.STARTING                     # before it is started
    assert watch.what_it_still_owes().state == H.UNAVAILABLE
    assert watch.what_it_still_owes().code == H.NOT_YET_PROBED
    assert not watch.what_it_still_owes().may_admit


def test_a_watch_that_is_never_started_still_admits(tmp_path):
    """The counter-check: an in-process worker cannot be lost without the gateway going with it,
    and every gateway built in a test is one. Blocking those would say a machine was broken for
    having nothing to probe."""
    watch, _clock = _watch(_there)

    assert watch.now().state == H.STARTING
    assert watch.now().may_admit


def test_a_started_gateway_does_not_admit_before_its_first_probe_and_then_does(tmp_path):
    """What the conservative start is for, and what it is not for.

    It is for not admitting before the first probe -- `MTLS-DEFAULT-R2-0001` found that hole on
    H3. It is NOT for forcing a fresh conformance measurement at every start: a version of this
    did that, on a misreading of HEALTH-HONESTY-0002's deployment section, and it meant every
    gateway serving over a transport refused until a suite it may not be responsible for running
    had succeeded. Where nothing runs that suite, for ever. CI found it across the whole mTLS
    lane in one go.
    """
    measured: list = []
    watch, _clock = _watch(_there, lambda: measured.append(True) or True,
                           publish_to=tmp_path / H.HEALTH_FILE)
    began = watch.what_it_still_owes()
    watch._publish(began.state, began.code, began.reason)

    assert began.state == H.UNAVAILABLE and began.code == H.NOT_YET_PROBED
    assert not began.may_admit, "nothing is admitted before the first probe"

    watch.consider(watch.probe_once())

    assert watch.now().state == H.PROTECTED
    assert watch.now().may_admit
    assert measured == [], (
        "a start that was owed nothing must not force a measurement; what decides whether the "
        "stored report still counts is the readiness gate, which runs on every admission")


def test_but_a_start_that_owes_a_measurement_still_takes_one(tmp_path):
    """The counter-check for the one above: the carried loss must still force it."""
    measured: list = []
    where = tmp_path / H.HEALTH_FILE
    first, _c = _watch(_gone, publish_to=where)
    first.turn()                                               # publishes the loss

    second, _c2 = _watch(_there, lambda: measured.append(True) or True, publish_to=where)
    began = second.what_it_still_owes()
    second._publish(began.state, began.code, began.reason)
    assert began.state == H.MEASURING

    second.turn()

    assert measured == [True]
    assert second.now().state == H.PROTECTED


def test_a_measurement_that_keeps_failing_is_not_retried_on_every_probe():
    """Found on the isolated pair, with a policy the sandbox cannot satisfy.

    The state flickered `measurement_failed` -> `measuring` -> `measurement_failed` every ten
    seconds, and each of those `measuring`s ran a whole conformance suite: containers started, an
    egress proxy built and torn down, for ever. Two things wrong with that. A caller asking twice
    a few seconds apart got two different causes for one unchanging situation, which is the
    opposite of what H4 is for. And the machine did minutes of work per minute while unwell.
    """
    tries: list = []
    clock = _Clock()
    watch = H.HealthWatch(_there, lambda: tries.append(clock.t) or False,
                          clock=clock, wall=lambda: clock.t)
    watch.turn()
    watch._reach = _gone
    watch.turn()
    watch._reach = _there
    watch.turn()                                               # -> measuring -> it fails
    assert watch.now().code == H.MEASUREMENT_FAILED
    assert len(tries) == 1

    # Every probe that falls inside the wait. None of them may try again.
    inside = int(H.RETRY_A_FAILED_MEASUREMENT_AFTER // H.PROBE_EVERY_SECONDS) - 1
    assert inside >= 1, "the wait has to be longer than one probe or this proves nothing"
    for _ in range(inside):
        clock.tick(H.PROBE_EVERY_SECONDS)
        watch.turn()
        assert watch.now().code == H.MEASUREMENT_FAILED, "and it says the same thing throughout"
    assert tries == tries[:1], "one attempt, not %d" % (inside + 1)

    # And after the wait, exactly one more.
    clock.tick(H.RETRY_A_FAILED_MEASUREMENT_AFTER)
    watch.turn()
    assert len(tries) == 2


def test_and_one_that_starts_working_is_believed_at_once():
    """The counter-check: the wait must not keep a recovered machine out of service."""
    answers = iter([False, True, True, True])
    clock = _Clock()
    watch = H.HealthWatch(_there, lambda: next(answers), clock=clock, wall=lambda: clock.t)
    watch.turn()
    watch._reach = _gone
    watch.turn()
    watch._reach = _there
    watch.turn()
    assert watch.now().code == H.MEASUREMENT_FAILED

    clock.tick(H.RETRY_A_FAILED_MEASUREMENT_AFTER)
    watch.turn()

    assert watch.now().state == H.PROTECTED
