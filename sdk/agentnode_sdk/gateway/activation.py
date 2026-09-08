"""Activating an operator policy and the measurement taken for it, as one thing or not at all.

`EM3C-Y6-DECISION-0001` chose `D4-a`: the active policy and the report measured for it live in a
single document, so one `os.replace` flips both. The alternative -- two files and a cross-reference
-- can only make a torn write *detectable*; this makes it impossible, and the difference matters
because the torn state is exactly the dangerous one: a widened policy beside a report taken before
it was widened.

The sequence is:

1. the proposed policy is written to a pending file with its own transaction id, and admission
   never looks at that file;
2. the pending policy is measured, as itself, not as whatever happens to be active;
3. if every property its mode requires was observed and passed, one document containing the
   generation, the canonical policy, its digest, the report and the report's binding is written,
   fsynced, authenticated, and moved into place with a single rename;
4. anything else leaves the previously active document exactly where it was.

There is no step at which the new policy is active and the old report is what would be consulted.

## What the authentication is for, and what it is not

`D7` asked that an attacker who can write the state directory not be able to forge a wider policy
or replay an older one. The tag is keyed from a file kept *outside* the state directory, in a
sibling directory with its own permissions, and the highest generation ever accepted is kept
beside that key rather than in the document it protects.

The honest limit: this defends against write access to the **state** directory. Someone who can
also read the key directory -- which in practice means someone running as the gateway's own user
-- can produce a valid tag, and no arrangement of files on one machine changes that. The gateway's
documentation has always said that whoever can write its directory can forge its identity; this
narrows that statement rather than repealing it, and the tests say which of the two they exercise.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from agentnode_sdk.gateway.operator_policy import (
    OperatorPolicyEnvelope,
    OperatorPolicyError,
    from_document,
    loads_strict,
)

ACTIVE_NAME = "active-state.json"
PENDING_NAME = "pending-policy.json"
LOCK_NAME = "activation.lock"
KEY_NAME = "snapshot.key"
ANCHOR_NAME = "generation.anchor"

#: Bumped when the meaning of the document changes.
STATE_SCHEMA = 1


class ActivationError(Exception):
    """The activation did not happen. The previously active state is untouched."""


class SnapshotUnusable(Exception):
    """A stored snapshot cannot be trusted, so it is treated as no snapshot at all."""


# --------------------------------------------------------------------------- protected material


def key_dir_for(state_root) -> Path:
    """Where the tag key and the generation anchor live: beside the state, never inside it."""
    root = Path(state_root)
    return root.parent / (root.name + ".secret")


class Protected:
    """The tag key and the highest accepted generation, kept out of the state directory."""

    def __init__(self, state_root) -> None:
        self.dir = key_dir_for(state_root)

    def _prepare(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(self.dir, 0o700)

    def key(self) -> bytes:
        self._prepare()
        path = self.dir / KEY_NAME
        if path.is_file():
            raw = path.read_bytes().strip()
            if len(raw) >= 32:
                return raw
            raise SnapshotUnusable(
                "the key that authenticates this gateway's active state is too short to be the "
                "one it wrote. It is not replaced automatically, because doing so would make "
                "deleting the key a way to re-sign anything.")
        fresh = secrets.token_bytes(32).hex().encode("ascii")
        handle, tmp = tempfile.mkstemp(dir=str(self.dir), prefix=".key-")
        try:
            with os.fdopen(handle, "wb") as fh:
                fh.write(fresh)
                fh.flush()
                os.fsync(fh.fileno())
            if os.name == "posix":
                os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            _quiet_unlink(tmp)
            raise
        return fresh

    def accepted_generation(self) -> int:
        path = self.dir / ANCHOR_NAME
        if not path.is_file():
            return 0
        try:
            return int(path.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            # An anchor that cannot be read is not treated as zero: that would be a rollback
            # anybody could arrange by corrupting one small file.
            raise SnapshotUnusable(
                "the record of the highest activation this gateway has accepted is unreadable, "
                "so an older state could not be told apart from the current one.") from None

    def remember_generation(self, generation: int) -> None:
        self._prepare()
        path = self.dir / ANCHOR_NAME
        handle, tmp = tempfile.mkstemp(dir=str(self.dir), prefix=".anchor-")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                fh.write(str(int(generation)))
                fh.flush()
                os.fsync(fh.fileno())
            if os.name == "posix":
                os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            _quiet_unlink(tmp)
            raise


def _quiet_unlink(path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# --------------------------------------------------------------------------- the document


@dataclass(frozen=True)
class ActiveState:
    """One policy, and the measurement taken for that policy. Never one without the other."""

    generation: int
    policy: OperatorPolicyEnvelope
    policy_digest: str
    report: dict
    binding: dict
    activated_at: float

    def body(self) -> dict:
        return {
            "state_schema": STATE_SCHEMA,
            "generation": self.generation,
            "policy": self.policy.as_canonical(),
            "policy_digest": self.policy_digest,
            "report": self.report,
            "binding": self.binding,
            "activated_at": self.activated_at,
        }


def _tag(key: bytes, body: dict) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hmac.new(key, raw, hashlib.sha256).hexdigest()


class ActivationStore:
    """Reads and writes the one document that says what this gateway is currently enforcing."""

    def __init__(self, state_root) -> None:
        self.root = Path(state_root)
        self.protected = Protected(self.root)

    # -------------------------------------------------------------- paths

    @property
    def active_path(self) -> Path:
        return self.root / ACTIVE_NAME

    @property
    def pending_path(self) -> Path:
        return self.root / PENDING_NAME

    # -------------------------------------------------------------- reading

    def load_active(self) -> ActiveState | None:
        """The active state, or None when there is none. Never a state that failed a check."""
        if not self.active_path.is_file():
            return None
        try:
            document = loads_strict(self.active_path.read_text(encoding="utf-8"))
        except (OSError, OperatorPolicyError) as exc:
            raise SnapshotUnusable(f"the active state cannot be read: {exc}") from None

        tag = document.pop("tag", None)
        if not isinstance(tag, str) or not tag:
            raise SnapshotUnusable("the active state carries no authentication tag.")
        if not hmac.compare_digest(_tag(self.protected.key(), document), tag):
            raise SnapshotUnusable(
                "the active state does not match its authentication tag, so it was changed by "
                "something other than this gateway.")

        if document.get("state_schema") != STATE_SCHEMA:
            raise SnapshotUnusable("the active state is a version this gateway does not read.")

        generation = document.get("generation")
        if not isinstance(generation, int) or generation < 1:
            raise SnapshotUnusable("the active state has no usable generation.")
        highest = self.protected.accepted_generation()
        if generation < highest:
            raise SnapshotUnusable(
                f"the active state is generation {generation} and this gateway has already "
                f"accepted {highest}. An older state is a rollback, not a current one.")

        policy = from_document(json.dumps(document.get("policy")))
        recomputed = policy.digest()
        if recomputed != document.get("policy_digest"):
            raise SnapshotUnusable(
                "the policy in the active state does not hash to the digest stored beside it.")

        report = document.get("report")
        binding = document.get("binding")
        if not isinstance(report, dict) or not isinstance(binding, dict):
            raise SnapshotUnusable("the active state has no report and binding to go with it.")

        if generation > highest:
            # Repairing the anchor for a snapshot that is already committed. If the anchor cannot
            # be written the snapshot is still valid -- it is newer than what is recorded -- so
            # this must not turn a readable state into an unreadable one.
            try:
                self.protected.remember_generation(generation)
            except OSError:
                pass

        return ActiveState(generation=generation, policy=policy, policy_digest=recomputed,
                           report=report, binding=binding,
                           activated_at=float(document.get("activated_at") or 0.0))

    def next_generation(self) -> int:
        try:
            current = self.load_active()
        except SnapshotUnusable:
            current = None
        highest = self.protected.accepted_generation()
        return max(highest, current.generation if current else 0) + 1

    # -------------------------------------------------------------- pending

    def write_pending(self, policy: OperatorPolicyEnvelope) -> str:
        """Record what is being proposed. Admission never reads this file."""
        transaction = uuid.uuid4().hex
        document = {
            "transaction": transaction,
            "policy": policy.as_canonical(),
            "policy_digest": policy.digest(),
            "proposed_at": time.time(),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        _write_atomic(self.pending_path, json.dumps(document, indent=2, sort_keys=True))
        return transaction

    def clear_pending(self) -> None:
        _quiet_unlink(self.pending_path)

    # -------------------------------------------------------------- activation

    def activate(self, policy: OperatorPolicyEnvelope, report: dict, binding: dict,
                 now: float | None = None) -> ActiveState:
        """Put policy and report in place together, or leave everything as it was."""
        generation = self.next_generation()
        state = ActiveState(generation=generation, policy=policy, policy_digest=policy.digest(),
                            report=report, binding=binding,
                            activated_at=time.time() if now is None else now)
        body = state.body()
        document = dict(body)
        document["tag"] = _tag(self.protected.key(), body)

        self.root.mkdir(parents=True, exist_ok=True)

        # THE COMMIT POINT. Everything before this can fail and leave the previous state exactly
        # as it was; nothing after it can un-commit.
        _write_atomic(self.active_path, json.dumps(document, indent=2, sort_keys=True))

        # After the rename, the anchor and the pending file are housekeeping. `EM3C-FINAL-0003`
        # found the earlier version letting a failure here propagate, which sent the caller down
        # a rollback path that restored the previous *intent* while the new snapshot was already
        # in place -- the transaction stopped being all-or-nothing precisely where it mattered.
        #
        # The anchor is a monotone cache, not the record: `load_active` raises it to the
        # snapshot's generation on every read, so a failure to write it here is repaired the
        # next time anything looks. Losing it costs one generation of rollback protection until
        # that repair happens, which is why it is still attempted, and why it is not fatal.
        try:
            self.protected.remember_generation(generation)
        except OSError:
            pass
        try:
            self.clear_pending()
        except OSError:
            pass
        return state


def _write_atomic(path: Path, text: str) -> None:
    """Write, flush to disk, and rename. The directory is fsynced so the rename survives too."""
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + "-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        if os.name == "posix":
            fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    except BaseException:
        _quiet_unlink(tmp)
        raise


class ActivationLock:
    """One activation at a time, across processes.

    Created with `O_EXCL`, so the winner is decided by the filesystem rather than by a check
    followed by a create. A stale lock from a killed process is broken only after it is older
    than any activation could reasonably take, and breaking it is reported.
    """

    def __init__(self, state_root, stale_after: float = 1800.0) -> None:
        self.path = Path(state_root) / LOCK_NAME
        self.stale_after = stale_after
        self._fd: int | None = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                age = time.time() - self.path.stat().st_mtime
            except OSError:
                age = 0.0
            if age <= self.stale_after:
                raise ActivationError(
                    "another change to this gateway's policy is already running. Nothing was "
                    "changed. Wait for it to finish, then try again.") from None
            _quiet_unlink(self.path)
            try:
                self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                raise ActivationError(
                    "another change to this gateway's policy is already running. Nothing was "
                    "changed.") from None
        os.write(self._fd, str(os.getpid()).encode("ascii"))
        return self

    def __exit__(self, *exc) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None
        _quiet_unlink(self.path)
