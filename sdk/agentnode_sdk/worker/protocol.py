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
METHODS = ("describe", "run", "stop", "gone", "measure", "measure_egress")

#: Every way this can go wrong, named. A caller gets one of these and never a sentence to parse.
#: `EM3C-EVIDENCE-0002`: the difference between "the answer is no" and "there was no answer" is
#: the difference these names exist to keep.
UNAUTHENTICATED = "unauthenticated"          # the MAC did not verify
MALFORMED = "malformed"                      # the bytes are not a message this build describes
STALE = "stale"                              # its clock is too far from ours
REPLAY = "replay"                            # we have seen this one before
TOO_LARGE = "too-large"                      # the frame is bigger than anyone may send
UNKNOWN_METHOD = "unknown-method"
BAD_PARAMS = "bad-params"
DEADLINE_PASSED = "deadline-passed"          # it was already too late when it arrived
RUNTIME_ABSENT = "runtime-absent"            # there is nothing here that can isolate anything
JOB_FAILED = "job-failed"                    # it was run and it did not work
NETWORK_UNAVAILABLE = "network-unavailable"  # the restricted network could not be built
INTERNAL = "internal"                        # the worker broke, and says so rather than hanging

ERRORS = (UNAUTHENTICATED, MALFORMED, STALE, REPLAY, TOO_LARGE, UNKNOWN_METHOD, BAD_PARAMS,
          DEADLINE_PASSED, RUNTIME_ABSENT, JOB_FAILED, NETWORK_UNAVAILABLE, INTERNAL)


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
    """

    def __init__(self, memory: float = NONCE_MEMORY_SECONDS) -> None:
        self.memory = memory
        self._when: dict[str, float] = {}

    def again(self, nonce: str, now: float | None = None) -> bool:
        """True when this one has been seen. Records it either way."""
        at = time.time() if now is None else now
        self._when = {n: t for n, t in self._when.items() if t > at - self.memory}
        if nonce in self._when:
            return True
        self._when[str(nonce)] = at
        return False


def check(body: dict[str, Any], seen: Seen, now: float | None = None) -> None:
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
    if seen.again(str(body["nonce"]), at):
        raise ProtocolError(REPLAY, "this message has been seen before")
    if body["method"] not in METHODS:
        raise ProtocolError(UNKNOWN_METHOD, str(body["method"])[:40])
    if not isinstance(body.get("params"), dict):
        raise ProtocolError(BAD_PARAMS, "parameters are an object")


# ------------------------------------------------------------------------------ bytes in JSON

def as_text(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def from_text(text: str) -> bytes:
    try:
        return base64.b64decode(str(text).encode("ascii"), validate=True)
    except Exception as exc:                                  # noqa: BLE001
        raise ProtocolError(BAD_PARAMS, "a field that should carry bytes does not") from exc
