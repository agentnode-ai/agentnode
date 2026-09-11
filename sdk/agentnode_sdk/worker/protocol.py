"""What a message between the control plane and the worker is, and in what order it is believed.

The order is the design. A receiver establishes things about a frame in this sequence and stops at
the first that does not hold:

    1. its length, before anything is allocated for it
    2. its authenticity, over the bytes exactly as they arrived
    3. what it says, by parsing those bytes
    4. whether it is fresh, and whether it has been seen before
    5. whether the method exists and the parameters are the shape that method takes

Authenticating before parsing is not fussiness. A JSON parser is a large amount of code to expose
to anything that can connect to a socket; putting a MAC in front of it means an attacker without
the key never reaches it. And the MAC covers the bytes AS SENT rather than a re-serialisation of
what was parsed, so there is no second opinion about what was signed.

## What is on the wire

    [4 bytes, big-endian, the body's length][32 bytes, HMAC-SHA256 of the body][the body]

The body is canonical JSON: sorted keys, no spaces, UTF-8. Bytes inside it -- an artefact, a
result -- are base64, because JSON has no other way to carry them and because the alternative is a
second framing nobody would test.

## What is NOT on the wire

No command a shell would read. A job's `command` is an argv vector and stays one: the worker
passes it to a container runtime as a list, and nothing anywhere joins it into a string. That is
checked by a test rather than left as an intention.

## What this does not establish

A shared key on one machine is worth what the machine is worth: root can read it, and root can
read everything else too. What it buys is that the protocol does not change when the worker moves
to another machine -- there the same MAC is over a TLS connection and the key is not on the same
disk as the thing it authenticates.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time
from pathlib import Path
from typing import Any

#: The version on every message. A receiver that does not know it refuses rather than guessing;
#: a protocol that silently accepts a message it does not understand is one that will one day run
#: something it did not mean to.
PROTOCOL = "agentnode-worker/1"

#: The most a single frame may be. Read before anything is allocated, so a length nobody meant is
#: refused rather than reserved. Large enough for an artefact and a job's whole output; a job that
#: produces more than this is a job whose output was never going to be read by a person.
MAX_FRAME = 16 * 1024 * 1024

#: How far apart the two clocks may be before a message is stale. Wide enough for an ordinary
#: skew on one machine, narrow enough that a captured message stops being useful quickly.
FRESHNESS_SECONDS = 120.0

#: How long a receiver remembers a message it has seen. Longer than the freshness window, or a
#: message could become new again by being old.
NONCE_MEMORY_SECONDS = FRESHNESS_SECONDS * 4

#: The only things that may be asked. A closed list, because a method a receiver does not know is
#: refused by name rather than dispatched to whatever happens to be there.
#: Every field a request may carry, and nothing else. Named here rather than checked field by
#: field, so that adding one is a decision somebody makes in a place a reviewer reads.
FIELDS = ("protocol", "request_id", "nonce", "method", "issued_at", "deadline", "params")

METHODS = ("describe", "run", "stop", "gone", "measure", "measure_egress")

#: Every way this can go wrong, named. A caller gets one of these and never a sentence to parse.
#: `EM3C-EVIDENCE-0002`: the difference between "the answer is no" and "there was no answer" is
#: the difference these names exist to keep.
UNAUTHENTICATED = "unauthenticated"          # the MAC did not verify
MALFORMED = "malformed"                      # the bytes are not a message this build describes
STALE = "stale"                              # its clock is too far from ours
REPLAY = "replay"                            # we have seen this one before
ROLLED_BACK = "rolled-back"                  # issued before something already accepted
TOO_LARGE = "too-large"                      # the frame is bigger than anyone may send
UNKNOWN_METHOD = "unknown-method"
BAD_PARAMS = "bad-params"
DEADLINE_PASSED = "deadline-passed"          # it was already too late when it arrived
RUNTIME_ABSENT = "runtime-absent"            # there is nothing here that can isolate anything
JOB_FAILED = "job-failed"                    # it was run and it did not work
NETWORK_UNAVAILABLE = "network-unavailable"  # the restricted network could not be built
INTERNAL = "internal"                        # the worker broke, and says so rather than hanging

ERRORS = (UNAUTHENTICATED, MALFORMED, STALE, REPLAY, ROLLED_BACK, TOO_LARGE, UNKNOWN_METHOD,
          BAD_PARAMS, DEADLINE_PASSED, RUNTIME_ABSENT, JOB_FAILED, NETWORK_UNAVAILABLE, INTERNAL)


class ProtocolError(Exception):
    """A message could not be believed. Carries which of `ERRORS` it was."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code + (": " + detail if detail else ""))
        self.code = code
        self.detail = detail


# ------------------------------------------------------------------------------ the key

def read_key(path: str | os.PathLike[str]) -> bytes:
    """The shared key, or a refusal that says why.

    Refusing here rather than starting without one is the whole point: a worker that could not
    read its key and served anyway would be a worker with no authentication at all, and the
    operator would have no way to notice.
    """
    try:
        raw = open(path, "rb").read().strip()
    except OSError as exc:
        raise ProtocolError(
            UNAUTHENTICATED,
            "the key that authenticates messages between the gateway and the worker could not be "
            "read at " + str(path) + " (" + str(exc) + "). Neither side starts without it.") from exc
    if len(raw) < 32:
        raise ProtocolError(
            UNAUTHENTICATED,
            "the key at " + str(path) + " is shorter than 32 bytes, which is not a key. Make one "
            "with:  agentnode worker key --at " + str(path))
    return raw


def new_key() -> bytes:
    """From the same source the gateway's own credentials come from."""
    return base64.urlsafe_b64encode(secrets.token_bytes(48))


# ------------------------------------------------------------------------- bytes on the wire

def canonical(body: dict[str, Any]) -> bytes:
    """One serialisation of a message, so that both sides authenticate the same bytes."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def seal(body: dict[str, Any], key: bytes) -> bytes:
    """A whole frame: its length, its MAC, and the body those two are about."""
    payload = canonical(body)
    if len(payload) > MAX_FRAME:
        raise ProtocolError(TOO_LARGE,
                            "this message is %d bytes and the most anyone may send is %d"
                            % (len(payload), MAX_FRAME))
    mac = hmac.new(key, payload, hashlib.sha256).digest()
    return struct.pack(">I", len(payload)) + mac + payload


def unseal(payload: bytes, mac: bytes, key: bytes) -> dict[str, Any]:
    """The message these bytes are, once they have been shown to be ours.

    In this order and no other: the MAC first, over the bytes as they arrived, and only then the
    parser. A comparison in constant time, because a MAC compared byte by byte tells whoever is
    guessing how far they got.
    """
    if not hmac.compare_digest(mac, hmac.new(key, payload, hashlib.sha256).digest()):
        raise ProtocolError(UNAUTHENTICATED,
                            "this message was not written by something holding the key")
    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError(MALFORMED, str(exc)) from exc
    if not isinstance(body, dict):
        raise ProtocolError(MALFORMED, "a message is an object")
    return body


def read_frame(stream, key: bytes) -> dict[str, Any]:
    """One frame off a stream, bounded before it is allocated for.

    `stream` is anything with `recv`-like `read`. A short read is not a small message: it is a
    message that has not all arrived, and this keeps reading until it has or the stream ends.
    """
    header = _exactly(stream, 4)
    (length,) = struct.unpack(">I", header)
    if length > MAX_FRAME:
        # Before the allocation, not after it. Nothing that can connect gets to decide how much
        # memory this process asks for.
        raise ProtocolError(TOO_LARGE,
                            "a frame announced %d bytes and the most anyone may send is %d"
                            % (length, MAX_FRAME))
    mac = _exactly(stream, 32)
    return unseal(_exactly(stream, length), mac, key)


def _exactly(stream, count: int) -> bytes:
    got = b""
    while len(got) < count:
        chunk = stream.read(count - len(got))
        if not chunk:
            raise ProtocolError(MALFORMED,
                                "the connection ended after %d of %d bytes" % (len(got), count))
        got += chunk
    return got


# ------------------------------------------------------------------------------ messages

def request(method: str, params: dict[str, Any], *, deadline: float,
            now: float | None = None) -> dict[str, Any]:
    """One question, with everything a receiver needs to decide whether to believe it."""
    if method not in METHODS:
        raise ProtocolError(UNKNOWN_METHOD, method)
    at = time.time() if now is None else now
    return {
        "protocol": PROTOCOL,
        "request_id": secrets.token_hex(16),
        # What makes this message different from every other one, so that sending it again is
        # something a receiver can notice.
        "nonce": secrets.token_hex(16),
        "issued_at": at,
        "deadline": float(deadline),
        "method": method,
        "params": params,
    }


def answer(request_id: str, result: Any) -> dict[str, Any]:
    return {"protocol": PROTOCOL, "request_id": request_id, "ok": True, "result": result}


def refusal(request_id: str, code: str, detail: str = "") -> dict[str, Any]:
    if code not in ERRORS:                                    # pragma: no cover - a typo guard
        code = INTERNAL
    return {"protocol": PROTOCOL, "request_id": request_id, "ok": False,
            "error": code, "detail": detail[:500]}


class Seen:
    """What a receiver remembers, so that the same message twice is not two messages.

    Forgetting is bounded by time rather than by count: a cache that forgot the oldest when it
    filled would be a cache an attacker could empty by sending enough messages, and then replay
    into.

    But WHICH time matters, and getting that wrong is what this class got wrong. Forgetting used
    to be measured against the wall clock, and the wall clock is a thing that can be set. A
    forward jump made every remembered nonce older than the window in one step, so the memory
    emptied; when the clock came back, a captured message satisfied freshness again and was no
    longer remembered. The defence against replay was removable by an attacker who could move a
    clock, which is a smaller ask than forging a MAC.

    So forgetting is measured against a clock nobody can set. `time.monotonic` only ever moves
    forward at its own rate; setting the system clock does not touch it. The wall-clock moment a
    message arrived is still recorded, because that is what a reader of this wants to know, but
    no decision rests on it.

    A monotonic clock resets when the process does, which leaves one gap: capture a message,
    restart the receiver, roll the clock back, replay. `Floor` closes that, and the two are meant
    to be used together -- see `check`.
    """

    def __init__(self, memory: float = NONCE_MEMORY_SECONDS, elapsed=None) -> None:
        self.memory = memory
        #: A clock that cannot be set backwards or forwards by anyone. Injectable so that a test
        #: can move it -- and, more to the point, so a test can move the WALL clock and show that
        #: nothing is forgotten.
        self._elapsed = elapsed or time.monotonic
        self._when: dict[str, tuple[float, float]] = {}

    def again(self, nonce: str, now: float | None = None) -> bool:
        """True when this one has been seen. Records it either way."""
        at = time.time() if now is None else now
        since = float(self._elapsed())
        # Evicted on elapsed time, never on `at`. `at` is what is REMEMBERED, not what decides.
        self._when = {n: (w, m) for n, (w, m) in self._when.items()
                      if since - m <= self.memory}
        if nonce in self._when:
            return True
        self._when[str(nonce)] = (at, since)
        return False

    def __len__(self) -> int:
        return len(self._when)


class Floor:
    """The oldest moment a receiver will still accept, which only ever moves forward.

    `Seen` cannot survive a restart -- a monotonic clock starts again with the process -- so
    without this, the sequence "capture a message, wait for a restart, set the clock back, send
    it again" works. Every check it has to pass would pass: the MAC is still valid, the nonce is
    no longer remembered, and a clock that has been moved back makes it fresh and its deadline
    unexpired.

    This is the part that does not forget. The highest `issued_at` ever accepted is written down,
    and nothing issued more than one freshness window before that is ever accepted again. Moving
    the clock back therefore does not buy an attacker a second chance; it buys a receiver that
    refuses everything, including the attacker, until the clock is right again. That is the
    correct direction for a thing to fail in.

    It is deliberately not a lock-out that needs clearing by hand: as soon as the clock is
    correct, the window covers the present again and the receiver carries on.
    """

    def __init__(self, path=None) -> None:
        self.path = Path(path) if path else None
        self._highest = 0.0
        if self.path is not None:
            try:
                self._highest = float(json.loads(self.path.read_text(encoding="utf-8"))["highest"])
            except (OSError, ValueError, KeyError, TypeError):
                # Absent is the ordinary case on a first run. Unreadable is not treated as a
                # reason to refuse everything: `Seen` still holds within this process, and a
                # receiver that would not start because of a corrupt hint is worse than one that
                # starts with the hint it can read.
                self._highest = 0.0

    @property
    def highest(self) -> float:
        return self._highest

    def too_old(self, issued_at: float, window: float) -> bool:
        """Whether this was issued so long before anything already accepted that it cannot be new."""
        return self._highest > 0.0 and float(issued_at) < self._highest - window

    def accepted(self, issued_at: float) -> None:
        """Note that this moment has been accepted. Only ever moves the floor forward."""
        if float(issued_at) <= self._highest:
            return
        self._highest = float(issued_at)
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".new")
            tmp.write_text(json.dumps({"highest": self._highest}), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:                                       # pragma: no cover - best effort
            pass


def check(body: dict[str, Any], seen: Seen, now: float | None = None,
          floor: "Floor | None" = None) -> None:
    """Everything about a request that is true before its method is even looked up."""
    at = time.time() if now is None else now
    if body.get("protocol") != PROTOCOL:
        raise ProtocolError(
            MALFORMED,
            "this message says it speaks " + repr(str(body.get("protocol"))[:40]) + " and this "
            "build speaks " + PROTOCOL + ". A receiver that guessed would one day guess wrong")
    for field in ("request_id", "nonce", "method"):
        if not isinstance(body.get(field), str) or not body.get(field):
            raise ProtocolError(MALFORMED, "a message has a " + field)
    for field in ("issued_at", "deadline"):
        if not isinstance(body.get(field), (int, float)):
            raise ProtocolError(MALFORMED, "a message says when it was written and until when it "
                                           "is worth answering")
    if abs(at - float(body["issued_at"])) > FRESHNESS_SECONDS:
        # Both directions. A message from the future is not less suspicious than an old one.
        raise ProtocolError(STALE,
                            "this message was written %.0fs from now, and anything more than "
                            "%.0fs apart is not answered" % (at - float(body["issued_at"]),
                                                             FRESHNESS_SECONDS))
    if float(body["deadline"]) <= at:
        raise ProtocolError(DEADLINE_PASSED,
                            "this message stopped being worth answering %.1fs ago"
                            % (at - float(body["deadline"])))
    # Before the nonce, because a message from before the floor is refused whether or not this
    # receiver happens to remember it -- and after a restart it remembers nothing.
    if floor is not None and floor.too_old(float(body["issued_at"]), FRESHNESS_SECONDS):
        raise ProtocolError(
            ROLLED_BACK,
            "this message was issued %.0fs before something this receiver has already accepted, "
            "which cannot happen to a new message. Either it is one that was captured earlier, "
            "or this machine's clock has been set back; neither is a reason to run it."
            % (floor.highest - float(body["issued_at"])))
    if seen.again(str(body["nonce"]), at):
        raise ProtocolError(REPLAY, "this message has been seen before")
    if body["method"] not in METHODS:
        raise ProtocolError(UNKNOWN_METHOD, str(body["method"])[:40])
    if not isinstance(body.get("params"), dict):
        raise ProtocolError(BAD_PARAMS, "parameters are an object")
    # A field this build does not describe is refused rather than ignored. Ignoring it is how two
    # builds come to disagree about what a message meant while both believe they understood it:
    # the sender puts something in that matters to it, the receiver drops it silently, and the
    # job runs under terms nobody agreed. Refusing is also what keeps the message that was
    # AUTHENTICATED and the message that was ACTED ON the same message.
    extra = sorted(set(body) - set(FIELDS))
    if extra:
        raise ProtocolError(
            MALFORMED,
            "this message carries " + ", ".join(repr(f) for f in extra[:4]) + ", which this "
            "build does not describe. A receiver that ignored them would be acting on less than "
            "it was sent")
    # Last, so that only a message which passed everything moves the floor. A malformed one that
    # happened to carry a far-future moment must not raise the bar for everything after it.
    if floor is not None:
        floor.accepted(float(body["issued_at"]))


# ------------------------------------------------------------------------------ bytes in JSON

def as_text(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def from_text(text: str) -> bytes:
    try:
        return base64.b64decode(str(text).encode("ascii"), validate=True)
    except Exception as exc:                                  # noqa: BLE001
        raise ProtocolError(BAD_PARAMS, "a field that should carry bytes does not") from exc
