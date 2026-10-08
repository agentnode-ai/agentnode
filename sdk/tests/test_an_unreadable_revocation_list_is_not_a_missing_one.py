"""A revocation list that cannot be READ is not a revocation list that is not THERE.

WHAT THIS IS FOR. The fourth independent round of the EG-closeout review left `EG19` at WARN on one finding
(`IR4-01`): an intermittent failure of
`test_mtls_revocation.py::TestOpenConnectionsAreReEvaluated::test_d_the_gateway_cuts_a_worker_revoked_under_a_running_job`
whose cause was established and whose repair had not been made. The cause is in this module's reader, not in
the publish path:

  * `pki/files.durable_publish` writes a stage, fsyncs it and `os.replace`s it onto the target. `os.replace`
    is atomic, and `issuer._publish_list` calls a revocation effective only once that promotion is durable.
    So there is no window in which the file is half-written.
  * but a reader that opens the file while root replaces it under them can still catch an error. Measured on
    one platform: a reader doing nothing but read that file took exceptions in their hundreds against a
    couple of thousand durable publications, with no absent and no empty read at all.
  * and `_bytes()` tells a missing file from an unreadable one -- its own docstring says so -- while
    `TrustView.read()` kept that reason for the FLOOR and dropped it for the LIST. So any read error arrived
    at `revoked_serials()` as `None`, and `None` is reported as "there is no revocation list to read".

An operator reading that is told to publish a list that is already there. The refusal itself was never
wrong: the peer is refused and the connection cut in every one of these states, which is why this was a
diagnostic defect and not a bypass.

THE ASYMMETRY IN ONE LINE: `test_mtls_floor.py::TestTheTrustViewTellsItsReasonsApart` has had a test that an
unreadable FLOOR is named as such since the floor's reason was kept. There was none for the list. This file
is that test, and the eight others the repair's bounds ask for.

WHAT IS DELIBERATELY NOT ASSERTED HERE. Nothing about elapsed time. The retry's bound is asserted as a
COUNT of attempts and as a sum of durations handed to a faked clock, so that no assertion in this file can
fail because a machine was busy. A test about a boundary that fails when the disk is slow teaches the next
person to re-run it until it passes.

HOW A READ IS MADE TO FAIL. `_bytes()` calls `open()`, which resolves through this module's globals before
the builtins, so a test can put its own callable there and take it away again. The fake fails only for the
path it is given, so the anchor and the floor are read normally and every refusal below is the one the test
is about rather than a refusal that happened first.
"""
from __future__ import annotations

import io
import os
import threading
import time
from pathlib import Path

import pytest

from agentnode_sdk.pki import files as pki_files
from agentnode_sdk.pki import floor as floors
from agentnode_sdk.pki import identity as ids
from agentnode_sdk.pki import revocation as rl
from agentnode_sdk.pki import trust as trust_module
from agentnode_sdk.pki.trust import TrustView
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout
from tests.test_mtls_transport import World

_REAL_OPEN = open


def serial_of(folder: Path) -> str:
    from cryptography import x509

    return format(x509.load_pem_x509_certificate((folder / "cert.pem").read_bytes())
                  .serial_number, "x")


class AnOpenThatFails:
    """`open`, except that it fails for ONE path a fixed number of times first.

    Counting the attempts is the point: it is what lets the retry's bound be asserted without a clock.
    """

    def __init__(self, path, times: int, error=PermissionError) -> None:
        self.path = os.fspath(path)
        self.times = times
        self.error = error
        self.attempts = 0

    def __call__(self, path, *args, **kwargs):
        if os.fspath(path) == self.path:
            self.attempts += 1
            if self.attempts <= self.times:
                raise self.error(13, "this read was made to fail by the test")
        return _REAL_OPEN(path, *args, **kwargs)


class ANoteOfEverySleep:
    """A stand-in for the clock. It records what it was asked to wait and waits for none of it."""

    def __init__(self) -> None:
        self.waits: list = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(float(seconds))


@pytest.fixture()
def world(tmp_path):
    return World(tmp_path)


@pytest.fixture()
def a_revoked_worker(world):
    """A worker whose certificate this World has revoked, and the published list that says so."""
    folder = world.service("worker", "w1")
    serial = serial_of(folder)
    done = world.issuer.revoke(serial)
    assert done["effective"] is True, "the World could not publish a list, so nothing below is about trust"
    return folder, serial


def a_view(world, *, revocation_list=None, role: str = "worker", instance: str = "w1") -> TrustView:
    """The view a service of this World would read, with the list path overridable."""
    return TrustView.read(
        anchor=world.anchor,
        revocation_list=world.revocation_list if revocation_list is None else revocation_list,
        floor=floors.path_for(world.floor_dir, role),
        role=role,
        identity="agentnode://%s/%s/%s" % (world.deployment, role, instance))


def refusal_from(view: TrustView, presented: str = "agentnode://d/worker/w1"):
    """The refusal this view gives, or an AssertionError in this file's own words if it gives none.

    `pytest.raises` would report "DID NOT RAISE", which is true and is not this test's language. A
    counter-check has to predeclare the phrase a removal will produce, and it should be a phrase the
    test chose rather than one the framework chose.
    """
    try:
        admitted = view.revoked_serials(time.time(), presented=presented)
    except ids.PeerRefused as refused:
        return refused
    raise AssertionError(
        "this state must be refused and was accepted instead, with: %r" % (sorted(admitted),))


# ============================================================ 1. a file that is genuinely missing

def test_1_a_revocation_list_that_is_genuinely_missing_says_so(world, a_revoked_worker):
    """ENOENT keeps its own meaning. This is the clause the repair must NOT change."""
    missing = Path(world.root) / "trust" / "not-published-yet.crl"
    assert not missing.exists()
    refused = refusal_from(a_view(world, revocation_list=missing))
    assert refused.check == rl.UNREADABLE, refused.check
    assert "there is no revocation list to read" in str(refused), \
        "a missing revocation list must still say there is no list to read"


# ======================================================== 2. a file that permanently cannot be read

def test_2_an_unreadable_revocation_list_does_not_say_it_is_missing(world, a_revoked_worker,
                                                                    monkeypatch):
    """The finding itself. The list IS there; every read of it fails; the refusal must not claim absence."""
    failing = AnOpenThatFails(world.revocation_list, times=10 ** 6)
    monkeypatch.setattr(trust_module, "open", failing, raising=False)
    monkeypatch.setattr(time, "sleep", ANoteOfEverySleep())
    refused = refusal_from(a_view(world))
    assert world.revocation_list.exists(), "the test removed the file it meant to make unreadable"
    assert "there is no revocation list to read" not in str(refused), \
        "an unreadable revocation list must not be reported as a missing one"
    assert "could not be read" in str(refused), \
        "an unreadable revocation list must say that it could not be read"
    assert "PermissionError" in str(refused), \
        "the refusal must carry the classified reason it was given"


# =================================================== 3. a transient failure, then a successful read

def test_3_a_transient_read_error_is_tried_again_and_the_list_is_read(world, a_revoked_worker,
                                                                      monkeypatch):
    """The liveness half. A read that fails once and would then succeed must not become a refusal."""
    _folder, serial = a_revoked_worker
    failing = AnOpenThatFails(world.revocation_list, times=1)
    slept = ANoteOfEverySleep()
    monkeypatch.setattr(trust_module, "open", failing, raising=False)
    monkeypatch.setattr(time, "sleep", slept)
    view = a_view(world)
    # THE REFUSAL IS CAUGHT AND TURNED INTO THIS TEST'S OWN WORDS. Left to propagate, the unrepaired
    # product fails this test by raising, and a red that carries the product's sentence instead of the
    # test's cannot be predeclared by a counter-check. A test should fail in language it chose.
    try:
        revoked = view.revoked_serials(time.time(), presented="agentnode://d/worker/w1")
    except ids.PeerRefused as refused:
        raise AssertionError(
            "a transient read error must be tried again, not turned into a refusal: %s" % refused)
    assert serial in revoked, \
        "a transient read error must be tried again, not turned into a refusal"
    assert failing.attempts >= 2, \
        "the list was opened only once, so nothing was tried again"
    assert len(slept.waits) == 1, \
        "one failed read then a good one must wait exactly once between them"
    assert sum(slept.waits) <= getattr(trust_module, "_RETRY_CEILING_SECONDS", 0.25), slept.waits


# ======================================================= 4. the tries run out and it refuses anyway

def test_4_when_the_tries_are_exhausted_it_still_refuses_fail_closed(world, a_revoked_worker,
                                                                      monkeypatch):
    """The safety half. Retrying may not become believing: after the tries, it still refuses."""
    failing = AnOpenThatFails(world.revocation_list, times=10 ** 6)
    monkeypatch.setattr(trust_module, "open", failing, raising=False)
    monkeypatch.setattr(time, "sleep", ANoteOfEverySleep())
    refused = refusal_from(a_view(world))
    assert refused.check == rl.UNREADABLE, refused.check
    assert "could not be read" in str(refused), \
        "after the tries are exhausted it must still refuse, with the honest cause"
    assert failing.attempts > 1, \
        "after the tries are exhausted it must still refuse, and it must have tried more than once"


# ========================================= 5. the list replaced atomically while it is being read

def test_5_nothing_stale_empty_or_partial_is_accepted_while_the_list_is_replaced(world,
                                                                                 a_revoked_worker):
    """Every reading taken while root replaces the list is either a refusal or the WHOLE list.

    The replacements all carry the same serial and a rising list number, so a reading that came back with a
    different serial set could only have come from a stale, empty or partly read file. Nothing here asserts
    that a transient error WILL happen -- on a quiet machine none may -- only that nothing untrue is
    accepted if one does.
    """
    _folder, serial = a_revoked_worker
    files = pki_files.Files()
    published = io.open(world.revocation_list, "rb").read()
    expected = frozenset(a_view(world).revoked_serials(time.time(),
                                                       presented="agentnode://d/worker/w1"))
    assert serial in expected, "the fixture did not publish a list naming the revoked serial"

    stop = threading.Event()
    counted = {"read": 0, "refused": 0, "wrong_set": 0}

    def replace_it_over_and_over():
        while not stop.is_set():
            try:
                pki_files.durable_publish(files, world.revocation_list, published, mode=0o644,
                                          label="test-list")
            except Exception:                                      # noqa: BLE001
                pass

    writer = threading.Thread(target=replace_it_over_and_over, daemon=True)
    writer.start()
    try:
        for _ in range(250):
            try:
                got = frozenset(a_view(world).revoked_serials(
                    time.time(), presented="agentnode://d/worker/w1"))
            except ids.PeerRefused:
                counted["refused"] += 1
                continue
            counted["read"] += 1
            if got != expected:
                counted["wrong_set"] += 1
    finally:
        stop.set()
        writer.join(timeout=10)

    assert counted["read"] + counted["refused"] == 250, counted
    assert counted["read"] > 0, \
        "not one reading succeeded, so this test did not exercise what it claims to"
    assert counted["wrong_set"] == 0, \
        "a reading taken while the list was replaced returned a different serial set"


# ============================================ 6. a revoked certificate during that same concurrency

def test_6_a_revoked_certificate_stays_revoked_while_the_list_is_replaced(world, a_revoked_worker):
    """The decision, not the bytes: a revoked serial is never seen as unrevoked. Refusing is allowed."""
    _folder, serial = a_revoked_worker
    files = pki_files.Files()
    published = io.open(world.revocation_list, "rb").read()

    stop = threading.Event()
    counted = {"revoked": 0, "refused": 0, "admitted": 0}

    def replace_it_over_and_over():
        while not stop.is_set():
            try:
                pki_files.durable_publish(files, world.revocation_list, published, mode=0o644,
                                          label="test-list")
            except Exception:                                      # noqa: BLE001
                pass

    writer = threading.Thread(target=replace_it_over_and_over, daemon=True)
    writer.start()
    try:
        for _ in range(250):
            try:
                revoked = a_view(world).revoked_serials(time.time(),
                                                        presented="agentnode://d/worker/w1")
            except ids.PeerRefused:
                counted["refused"] += 1
                continue
            if serial in revoked:
                counted["revoked"] += 1
            else:
                counted["admitted"] += 1
    finally:
        stop.set()
        writer.join(timeout=10)

    assert counted["revoked"] > 0, \
        "not one reading saw the revocation, so this test did not exercise what it claims to"
    assert counted["admitted"] == 0, \
        "a revoked certificate was not seen as revoked while the list was being replaced"


# ================================================================ 7. no state is ever accepted open

def test_7_no_unreadable_state_is_ever_accepted_fail_open(world, a_revoked_worker, monkeypatch,
                                                           tmp_path):
    """Every way the list can be unusable is a refusal, and none of them is an empty answer.

    An empty set would say "nobody is revoked", which is the one answer none of these states supports.
    """
    monkeypatch.setattr(time, "sleep", ANoteOfEverySleep())
    good = io.open(world.revocation_list, "rb").read()

    missing = tmp_path / "never-written.crl"
    empty = tmp_path / "empty.crl"
    empty.write_bytes(b"")
    garbage = tmp_path / "garbage.crl"
    garbage.write_bytes(b"-----BEGIN X509 CRL-----\nnot a list at all\n-----END X509 CRL-----\n")
    truncated = tmp_path / "truncated.crl"
    truncated.write_bytes(good[: max(1, len(good) // 3)])

    for what, path in (("missing", missing), ("empty", empty), ("garbage", garbage),
                       ("truncated", truncated)):
        refused = refusal_from(a_view(world, revocation_list=path))
        assert refused.check == rl.UNREADABLE, (what, refused.check)

    # and the same for a read that always fails, which is the state this repair is about
    failing = AnOpenThatFails(world.revocation_list, times=10 ** 6)
    monkeypatch.setattr(trust_module, "open", failing, raising=False)
    refused = refusal_from(a_view(world))
    assert refused.check == rl.UNREADABLE, \
        "a state that cannot be read was accepted instead of refused"


# ====================================================== 8. the two diagnoses are not the same words

def test_8_missing_and_unreadable_are_different_diagnoses(world, a_revoked_worker, monkeypatch):
    """The finding, stated as the comparison it is: the same sentence for two states is the defect."""
    monkeypatch.setattr(time, "sleep", ANoteOfEverySleep())
    missing = Path(world.root) / "trust" / "there-is-no-such-list.crl"
    said_when_missing = str(refusal_from(a_view(world, revocation_list=missing)))

    failing = AnOpenThatFails(world.revocation_list, times=10 ** 6)
    monkeypatch.setattr(trust_module, "open", failing, raising=False)
    said_when_unreadable = str(refusal_from(a_view(world)))

    assert said_when_missing != said_when_unreadable, \
        "missing and unreadable must not produce the same diagnosis"
    assert "no revocation list" in said_when_missing, said_when_missing
    assert "could not be read" in said_when_unreadable, said_when_unreadable


# ============================================ 9. the bound is a fixed ceiling, and ENOENT is exempt

def test_9_the_retry_bound_is_a_fixed_ceiling_and_enoent_is_not_retried(world, a_revoked_worker,
                                                                         monkeypatch):
    """The bound is a constant of the module, and it is asserted as a count, never as elapsed time."""
    tries = getattr(trust_module, "_TRIES", None)
    between = getattr(trust_module, "_BETWEEN_TRIES", None)
    ceiling = getattr(trust_module, "_RETRY_CEILING_SECONDS", None)
    assert isinstance(tries, int) and 2 <= tries <= 8, \
        "the retry bound must be a fixed ceiling and ENOENT must not be retried"
    assert isinstance(between, float) and 0 < between <= 0.1, \
        "the wait between tries must be a small fixed constant"
    assert ceiling == (tries - 1) * between and ceiling <= 0.25, \
        "the ceiling must be the fixed count times the fixed wait, and must stay small"

    # a read that always fails is opened exactly `tries` times and waits at most the ceiling
    failing = AnOpenThatFails(world.revocation_list, times=10 ** 6)
    slept = ANoteOfEverySleep()
    monkeypatch.setattr(trust_module, "open", failing, raising=False)
    monkeypatch.setattr(time, "sleep", slept)
    refusal_from(a_view(world))
    assert failing.attempts == tries, \
        "a read that always fails must be attempted exactly the fixed number of times"
    assert len(slept.waits) == tries - 1, \
        "there must be exactly one wait between consecutive tries"
    assert sum(slept.waits) <= ceiling, \
        "the total wait must not exceed the fixed ceiling"

    # and a file that is NOT there is asked for once: it will not appear by being asked again
    absent = AnOpenThatFails(Path(world.root) / "trust" / "absent.crl", times=10 ** 6,
                             error=FileNotFoundError)
    slept_for_absent = ANoteOfEverySleep()
    monkeypatch.setattr(trust_module, "open", absent, raising=False)
    monkeypatch.setattr(time, "sleep", slept_for_absent)
    refusal_from(a_view(world, revocation_list=Path(world.root) / "trust" / "absent.crl"))
    assert absent.attempts == 1, \
        "a file that is not there must be asked for once: ENOENT must not be retried"
    assert slept_for_absent.waits == [], slept_for_absent.waits


# ===================================== 10. the retry reads the NEWEST list, never a remembered one

class AnOpenThatPublishesThenFails:
    """`open`, except that the first attempt on ONE path publishes a NEW list and then fails.

    This puts a replacement exactly inside the retry window, deterministically, with no sleep and no race:
    the first attempt fails, and by the time the second happens the file on disk is a newer generation.
    """

    def __init__(self, path, publish) -> None:
        self.path = os.fspath(path)
        self.publish = publish
        self.attempts = 0
        self.published = False

    def __call__(self, path, *args, **kwargs):
        if os.fspath(path) == self.path:
            self.attempts += 1
            if self.attempts == 1:
                self.publish()
                self.published = True
                raise PermissionError(13, "this read was made to fail by the test")
        return _REAL_OPEN(path, *args, **kwargs)


def test_10_a_retry_reads_the_newest_list_and_never_a_remembered_one(world, a_revoked_worker,
                                                                      monkeypatch):
    """The clause `DECISION-0001` makes measurable: during the retries nothing old is accepted.

    A retry that succeeds does so at a LATER instant than the attempt that failed, so the file it reads is
    newer or the same -- never older. This proves it the only way that distinguishes it from a remembered
    value: the list is REPLACED between the failed attempt and the successful one, and the replacement
    revokes a serial the first version did not name. A reader that served anything remembered, or the file
    as it was before the failure, cannot see that serial.
    """
    _folder, first_serial = a_revoked_worker
    second = world.service("worker", "w2")
    second_serial = serial_of(second)

    # a successful read first, so that an implementation WITH a memory has something to remember
    before = frozenset(a_view(world).revoked_serials(time.time(),
                                                     presented="agentnode://d/worker/w1"))
    assert first_serial in before, "the fixture did not revoke the first worker"
    assert second_serial not in before, "the second worker is revoked before the test revoked it"

    def publish_a_newer_list():
        done = world.issuer.revoke(second_serial)
        assert done["effective"] is True, "the newer list was not published, so this test proves nothing"

    failing = AnOpenThatPublishesThenFails(world.revocation_list, publish_a_newer_list)
    slept = ANoteOfEverySleep()
    monkeypatch.setattr(trust_module, "open", failing, raising=False)
    monkeypatch.setattr(time, "sleep", slept)
    try:
        after = frozenset(a_view(world).revoked_serials(time.time(),
                                                        presented="agentnode://d/worker/w1"))
    except ids.PeerRefused as refused:
        raise AssertionError(
            "a read that failed once must be tried again and read the list that is there then: %s"
            % refused)
    assert failing.published, "the test did not manage to replace the list inside the retry window"
    # THE DISCRIMINATING ASSERTION COMES FIRST, and the preconditions after it. Ordered the other way, a
    # product that served a remembered value failed this test on "nothing was tried again" -- true, because
    # a fallback short-circuits the retry, and the wrong thing to name: the counter-check for that removal
    # has to predeclare a phrase about REMEMBERING. A red should name the property that broke.
    assert second_serial in after, \
        "the retry served an older or remembered list: the serial revoked during the retry is missing"
    assert first_serial in after, "the newer list lost a revocation the older one had"
    assert failing.attempts >= 2, "nothing was tried again, so no replacement could have been read"


# ============================== 11. the floor and the list now carry their reason alike

def test_11_the_floor_and_the_list_both_carry_their_reason(world, a_revoked_worker, monkeypatch):
    """The asymmetry that was the whole finding, asserted as the comparison it is.

    `_bytes` has always told a missing file from an unreadable one. The FLOOR has carried that reason since a
    floor it could not trust became a refusal; the LIST threw it away. This asserts both halves in one test,
    so that the day one of them stops carrying it, something says so.
    """
    monkeypatch.setattr(time, "sleep", ANoteOfEverySleep())
    floor_path = floors.path_for(world.floor_dir, "worker")

    # the floor's half
    failing_floor = AnOpenThatFails(floor_path, times=10 ** 6)
    monkeypatch.setattr(trust_module, "open", failing_floor, raising=False)
    view = a_view(world)
    with pytest.raises(ids.PeerRefused) as caught:
        view.effective_time()
    said_about_the_floor = str(caught.value)
    assert "PermissionError" in said_about_the_floor, \
        "the floor must carry the classified reason it could not be read"

    # the list's half, in the same test, because the point is that they are alike
    failing_list = AnOpenThatFails(world.revocation_list, times=10 ** 6)
    monkeypatch.setattr(trust_module, "open", failing_list, raising=False)
    said_about_the_list = str(refusal_from(a_view(world)))
    assert "PermissionError" in said_about_the_list, \
        "the revocation list must carry the classified reason it could not be read, as the floor does"
    assert "there is no revocation list to read" not in said_about_the_list, \
        "the revocation list must not be reported as missing when it could not be read"
