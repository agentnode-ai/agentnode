"""One key per gateway-and-worker pair, instead of one key for everybody.

`worker/protocol.py` authenticates every frame with an HMAC, and until now the key for that was
a single file -- `/etc/agentnode/worker.key` -- with the same bytes on the gateway and on the
worker, covering every job and every customer. Its own docstring is honest about why that was
acceptable: *"A shared key on one machine is worth what the machine is worth."*

On one machine that is true. Move the worker to another machine and it stops being true, because
the key is then a real secret sitting on a host whose whole purpose is running other people's
code. It is the one credential that contradicts "the worker gets only what one job needs", and
`MTLS-DEFAULT-R2`'s successor decision says so: per-pair keys, each bound to both certificate
identities, rejected when used by another pair.

## How a key is chosen, and why nothing new goes on the wire

The obvious design puts a key identifier in the frame so the receiver knows which key to try.
This does not need one. By the time a frame is read, mutual TLS has already proved who the peer
is -- that is the whole point of the handshake, and `pki/identity.py` has already refused anyone
whose certificate does not name an instance this side accepts. So the key is selected by the
identity the handshake proved, before the first byte of the first frame is authenticated.

That means:

  * nothing about the key appears on the wire, in a log, or in evidence;
  * an attacker cannot select a different key by editing a field, because there is no field;
  * a record is usable only by the pair it names -- both names are required to find it, and the
    side asking supplies its OWN name from its own certificate, not from anything it was sent.

## Rotation

A record holds a `current` key and, during an overlap, the `previous` one. Sealing always uses
`current`; verification accepts either. That is what stops a rotation making in-flight work
unknowable: a job whose request was sealed with the old key still verifies while its answer is
sealed with the new one. Retiring the old key is a second, separate step, so the overlap is
something an operator ends deliberately rather than something that ends when they were not
looking.

## What this module refuses to do

It does not generate keys where they will be used. A worker host does not mint the credential
that authenticates the control plane to it. Generation is an administrative act (`agentnode
worker key --pair`), and this module reads, selects, rotates and writes -- it never invents a
key as a side effect of not finding one. A missing record is a refusal, because a missing record
under a remote topology means somebody has not finished the enrolment, and starting anyway would
mean starting with no authentication at all.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any

#: The smallest key this will accept, in bytes of raw material. The same floor `read_key` uses.
MIN_KEY_BYTES = 32

#: The file is read by one service account and by root, and by nothing else.
FILE_MODE = 0o600

#: The on-disk shape, so a later change is a visible one.
FORMAT = 1


class KeyringRefused(Exception):
    """No usable key for this pair. Carries a cause, like every other refusal in this arc."""

    def __init__(self, cause: str, because: str, what_to_do: str) -> None:
        super().__init__(because)
        self.cause = cause
        self.because = because
        self.what_to_do = what_to_do


NO_FILE = "keyring_unreadable"
NO_RECORD = "no_key_for_this_pair"
MALFORMED = "keyring_malformed"
TOO_SHORT = "key_too_short"


@dataclass(frozen=True)
class PairKey:
    """The key material for one gateway-and-worker pair, and nothing else.

    `__repr__` is overridden and `__str__` follows it, because this object ends up in tracebacks
    and a traceback is a log. Neither ever renders the key material.
    """

    gateway: str
    worker: str
    generation: int
    current: bytes
    previous: bytes = b""

    def accepted(self) -> tuple[bytes, ...]:
        """Every key a frame from this pair may have been sealed with. Current first, so the
        common case compares once."""
        return (self.current, self.previous) if self.previous else (self.current,)

    def names(self) -> str:
        return "%s<->%s generation %d" % (self.gateway, self.worker, self.generation)

    def __repr__(self) -> str:                                # pragma: no cover - a log shape
        return "<PairKey %s, overlap=%s>" % (self.names(), bool(self.previous))

    __str__ = __repr__


def _material(raw: Any, where: str) -> bytes:
    if not isinstance(raw, str) or not raw.strip():
        raise KeyringRefused(MALFORMED, "the key at %s is not a string" % where,
                             "Regenerate the keyring with: agentnode worker key --pair")
    try:
        got = base64.urlsafe_b64decode(raw.strip() + "=" * (-len(raw.strip()) % 4))
    except (ValueError, TypeError) as exc:
        raise KeyringRefused(MALFORMED, "the key at %s is not base64 (%s)" % (where, exc),
                             "Regenerate the keyring with: agentnode worker key --pair") from exc
    if len(got) < MIN_KEY_BYTES:
        raise KeyringRefused(
            TOO_SHORT,
            "the key at %s is %d bytes, and the least a key may be is %d"
            % (where, len(got), MIN_KEY_BYTES),
            "Regenerate the keyring with: agentnode worker key --pair")
    return got


class Keyring:
    """Every pair key this side holds. Usually one."""

    def __init__(self, pairs: dict[tuple[str, str], PairKey], *, source: str = "") -> None:
        self._pairs = dict(pairs)
        self.source = source

    # ------------------------------------------------------------------ reading and writing

    @classmethod
    def read(cls, path: str | os.PathLike[str]) -> "Keyring":
        try:
            raw = open(path, "rb").read()
        except OSError as exc:
            raise KeyringRefused(
                NO_FILE,
                "the per-pair keys that authenticate messages between this gateway and its "
                "worker could not be read at %s (%s). Neither side starts without them."
                % (path, exc),
                "Create the pair's key with:  agentnode worker key --pair "
                "<gateway>:<worker> --at %s" % path) from exc
        try:
            said = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise KeyringRefused(MALFORMED, "the keyring at %s is not JSON (%s)" % (path, exc),
                                 "Regenerate it with: agentnode worker key --pair") from exc
        if not isinstance(said, dict) or int(said.get("format") or 0) != FORMAT:
            raise KeyringRefused(
                MALFORMED,
                "the keyring at %s is not a format %d keyring" % (path, FORMAT),
                "Regenerate it with: agentnode worker key --pair")
        pairs: dict[tuple[str, str], PairKey] = {}
        for entry in said.get("pairs") or []:
            if not isinstance(entry, dict):
                raise KeyringRefused(MALFORMED, "a keyring entry is not an object",
                                     "Regenerate it with: agentnode worker key --pair")
            gateway = str(entry.get("gateway") or "")
            worker = str(entry.get("worker") or "")
            if not gateway or not worker:
                raise KeyringRefused(
                    MALFORMED,
                    "a keyring entry names %r as its gateway and %r as its worker; a key belongs "
                    "to a PAIR and both ends have to be named, or it is a shared key again."
                    % (gateway, worker),
                    "Regenerate it with: agentnode worker key --pair <gateway>:<worker>")
            where = "%s/%s" % (gateway, worker)
            pairs[(gateway, worker)] = PairKey(
                gateway=gateway, worker=worker,
                generation=int(entry.get("generation") or 1),
                current=_material(entry.get("current"), where),
                previous=(_material(entry.get("previous"), where + " (previous)")
                          if entry.get("previous") else b""))
        return cls(pairs, source=str(path))

    def write(self, path: str | os.PathLike[str]) -> None:
        """Replace the file, atomically, at 0600. The key material is the only thing in it."""
        body = {"format": FORMAT, "pairs": [
            {"gateway": p.gateway, "worker": p.worker, "generation": p.generation,
             "current": base64.urlsafe_b64encode(p.current).decode("ascii"),
             **({"previous": base64.urlsafe_b64encode(p.previous).decode("ascii")}
                if p.previous else {})}
            for p in sorted(self._pairs.values(), key=lambda p: (p.gateway, p.worker))]}
        temporary = "%s.new.%d" % (path, os.getpid())
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
        try:
            with os.fdopen(handle, "wb") as fh:
                fh.write(json.dumps(body, indent=1, sort_keys=True).encode("utf-8"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temporary, path)
            os.chmod(path, FILE_MODE)
        finally:
            if os.path.exists(temporary):                     # pragma: no cover - a failed write
                os.unlink(temporary)

    # ------------------------------------------------------------------ choosing one

    def for_pair(self, *, gateway: str, worker: str) -> PairKey:
        """The key for exactly this pair.

        BOTH NAMES ARE REQUIRED, and that is the check rather than a formality: the caller
        supplies its own name from its own certificate and the peer's name from the certificate
        the handshake proved. A record for another pair is not found, so it cannot be used --
        which is what "reject use by another pair" means when there is no key identifier on the
        wire to reject.
        """
        found = self._pairs.get((str(gateway), str(worker)))
        if found is None:
            raise KeyringRefused(
                NO_RECORD,
                "there is no key for the pair %s<->%s in %s. This side holds keys for: %s."
                % (gateway, worker, self.source or "the keyring",
                   ", ".join("%s<->%s" % k for k in sorted(self._pairs)) or "(none)"),
                "Enrol the pair, or correct which worker this gateway is bound to. A worker "
                "this side holds no key for is not reached with somebody else's key.")
        return found

    def pairs(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._pairs))

    # ------------------------------------------------------------------ rotation

    def rotate(self, *, gateway: str, worker: str, key: bytes) -> "Keyring":
        """A new current key, with the old one kept as the overlap. Returns a new Keyring.

        The overlap is what stops a rotation making in-flight work unknowable: a request already
        sealed with the old key still verifies while answers are sealed with the new one.
        """
        if len(key) < MIN_KEY_BYTES:
            raise KeyringRefused(TOO_SHORT,
                                 "a new key is at least %d bytes" % MIN_KEY_BYTES,
                                 "Generate one with: agentnode worker key --pair")
        was = self.for_pair(gateway=gateway, worker=worker)
        fresh = dict(self._pairs)
        fresh[(gateway, worker)] = PairKey(gateway=gateway, worker=worker,
                                           generation=was.generation + 1,
                                           current=key, previous=was.current)
        return Keyring(fresh, source=self.source)

    def retire_overlap(self, *, gateway: str, worker: str) -> "Keyring":
        """End the overlap. A deliberate second step, so it does not happen unwatched."""
        was = self.for_pair(gateway=gateway, worker=worker)
        fresh = dict(self._pairs)
        fresh[(gateway, worker)] = PairKey(gateway=was.gateway, worker=was.worker,
                                           generation=was.generation, current=was.current)
        return Keyring(fresh, source=self.source)

    def add(self, *, gateway: str, worker: str, key: bytes) -> "Keyring":
        if len(key) < MIN_KEY_BYTES:
            raise KeyringRefused(TOO_SHORT, "a key is at least %d bytes" % MIN_KEY_BYTES,
                                 "Generate one with: agentnode worker key --pair")
        fresh = dict(self._pairs)
        fresh[(str(gateway), str(worker))] = PairKey(
            gateway=str(gateway), worker=str(worker), generation=1, current=key)
        return Keyring(fresh, source=self.source)


def empty() -> Keyring:
    return Keyring({})


__all__ = ["FILE_MODE", "FORMAT", "Keyring", "KeyringRefused", "MALFORMED", "MIN_KEY_BYTES",
           "NO_FILE", "NO_RECORD", "PairKey", "TOO_SHORT", "empty"]
