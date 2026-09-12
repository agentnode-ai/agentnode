"""Gateway identity, pairing, and the tokens that come out of it.

`EM3C-ARCHITECTURE-0001` chose **T-C**: a gateway is measured, and what is measured is bound to an
identity and a version. That only means something if the identity is stable across restarts and
changes when the build changes -- otherwise a client cannot tell whether the report it holds
describes the gateway it is now talking to.

The pairing flow is the part a person actually performs, so it is shaped around what a person can
do rather than around what is easiest to implement:

1. the gateway prints a short **pairing code**, valid once and briefly;
2. the person types it into their client along with the address;
3. the client exchanges it for a **token** and never needs the code again.

The code is deliberately weak-looking and short-lived; the token is long and permanent. That is the
trade a person can actually make: something typeable, alive for minutes, exchanged once for
something strong. A code that never expired would be a password with none of a password's
protections, and there would be no reason for the exchange to exist at all.

No key or certificate is ever edited by hand. This module generates, stores and rotates everything.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from agentnode_sdk.gateway import securedir
from agentnode_sdk.gateway.throttle import Budget, Locked, Store, Throttle

#: A pairing code is typed by a human, so it avoids characters that look alike in most fonts.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_GROUPS, CODE_GROUP_LEN = 3, 4
PAIRING_TTL_SECONDS = 15 * 60


def hash_token(token: str) -> str:
    """How a token is named without being stored.

    One helper rather than the same expression written out at each call site: an identity check
    that hashes differently in two places is one that sometimes says no to the right client, and
    it would only surface under rotation or ownership -- the two paths where being wrong matters
    most.
    """
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


class PairingError(Exception):
    """The pairing attempt is refused. The message says what a person can do about it."""


def new_pairing_code() -> str:
    """`XXXX-XXXX-XXXX`, from a CSPRNG. 32^12 is far beyond guessing inside a 15-minute window."""
    groups = [
        "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_GROUP_LEN))
        for _ in range(CODE_GROUPS)
    ]
    return "-".join(groups)


def normalise_code(raw: str) -> str:
    """Accept what a person plausibly types: any case, spaces, missing or extra dashes."""
    cleaned = "".join(ch for ch in str(raw or "").upper() if ch in _CODE_ALPHABET)
    if len(cleaned) != CODE_GROUPS * CODE_GROUP_LEN:
        raise PairingError(
            f"a pairing code is {CODE_GROUPS * CODE_GROUP_LEN} characters "
            f"(like ABCD-EFGH-JKLM); this one has {len(cleaned)}"
        )
    return "-".join(
        cleaned[i:i + CODE_GROUP_LEN] for i in range(0, len(cleaned), CODE_GROUP_LEN)
    )


@dataclass(frozen=True)
class GatewayIdentity:
    """What a client is talking to. T-C binds every measurement to exactly this."""

    gateway_id: str
    version: str

    def as_dict(self) -> dict[str, str]:
        return {"gateway_id": self.gateway_id, "version": self.version}

    @property
    def fingerprint(self) -> str:
        """Identity and version together. A change to either produces a different value.

        That is the point: a report describes a build, and a gateway that upgraded is a different
        build even though it is the same machine.
        """
        return hashlib.sha256(
            f"{self.gateway_id}\n{self.version}".encode()
        ).hexdigest()


class GatewayState:
    """The gateway's own files: its identity, its tokens, its live pairing code.

    Everything lives under one directory that the gateway owns, is created with restrictive
    permissions, and is never meant to be edited by hand.
    """

    def __init__(self, root: str | os.PathLike[str], version: str) -> None:
        self.root = Path(root)
        self.version = version
        self.root.mkdir(parents=True, exist_ok=True)
        self._harden(self.root)
        self._identity_path = self.root / "identity.json"
        self._tokens_path = self.root / "tokens.json"
        self._pairing: tuple[str, float] | None = None
        # One lock covers issuing and redeeming. Without it two concurrent redemptions can
        # both read the live code before either clears it, and a single-use code is used
        # twice -- the exact property the code is supposed to have.
        # P1-A (EM3C-STATEDIR-DECISION-0001): the directory is opened and judged once, and the
        # DESCRIPTOR is kept. Every secret operation is relative to it, so renaming, replacing or
        # symlinking the path afterwards changes nothing about which inode is read -- and the
        # descriptor is re-judged before each use, so a change of owner or mode on that inode is
        # caught before the next secret is touched.
        self._dir_fd = None
        self.state_verifiable = securedir.SUPPORTED
        if securedir.SUPPORTED:
            self._dir_fd = securedir.open_state_dir(self.root)
        self._pairing_lock = threading.Lock()
        self._pairing_path = self.root / "pairing.json"
        # The counters go through the same verified descriptor as every other secret: one of
        # them decides whether pairing is locked, so a pathname was the wrong footing for it.
        counters = Store(
            read=lambda name: self._read_private(name),
            write=lambda name, text: self._write_private(name, text),
        ) if securedir.SUPPORTED else None
        self._throttle = Throttle(
            store=counters,
            path=self.root / "pairing-throttle.json",
            # identity.json exists from the first time this gateway answered for itself, so its
            # presence separates "never had a failed attempt" from "someone removed the record".
            established_marker=self._identity_path,
        )
        # Counts attempts rather than failures, so grinding is bounded even when every guess is
        # wrong in a way that would not trip the failure lockout.
        self._admission = Budget(
            store=counters,
            path=self.root / "pairing-admission.json",
            established_marker=self._identity_path,
        )

    @staticmethod
    def _harden(path: Path) -> None:
        """Owner-only. Best effort: on Windows the mode is largely advisory, and saying so is
        better than implying a guarantee the platform does not give."""
        try:
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
        except OSError:
            pass

    # ---------------------------------------------------------------- identity

    @property
    def identity(self) -> GatewayIdentity:
        """Stable across restarts, generated once. The version comes from the running build."""
        existing = self._read_private("identity.json")
        if existing is not None:
            try:
                data = json.loads(existing)
                gid = str(data["gateway_id"])
            except (OSError, ValueError, KeyError) as exc:
                raise PairingError(
                    f"the gateway's identity file is unreadable ({exc}). Remove "
                    f"{self._identity_path} and start the gateway again to issue a new identity; "
                    "every paired client will have to pair again."
                ) from exc
        else:
            gid = secrets.token_hex(16)
            self._write_private("identity.json", json.dumps({"gateway_id": gid}, indent=2))
            # Written together with the marker, so from here on an absent throttle file means
            # somebody removed it rather than that nothing has been recorded yet.
            self._throttle.ensure_initialised()
            self._admission.ensure_initialised()
        return GatewayIdentity(gateway_id=gid, version=self.version)

    # ---------------------------------------------------------------- pairing

    def start_pairing(self, now: float | None = None) -> str:
        """Issue a pairing code. Only one is live at a time: a second call replaces the first.

        Replacing rather than accumulating is deliberate. Several live codes would each be a way
        in, and a person who pressed the button twice would have no idea how many were valid.
        """
        now = time.time() if now is None else now
        code = new_pairing_code()
        expires = now + PAIRING_TTL_SECONDS
        with self._pairing_lock:
            self._pairing = (code, expires)
            # Also on disk, because `agentnode gateway pair` and `agentnode gateway start` are
            # separate processes. A code held only in the memory of whoever issued it could never
            # reach the process that has to accept it -- the CLI would have been unusable in the
            # one shape everybody actually runs it in.
            #
            # The HASH is stored, not the code. The file is what an attacker with a copy of the
            # gateway directory gets, and a pairing code sitting in it in plain text would make
            # that copy a working key for fifteen minutes.
            self._write_pairing({"code_sha256": hash_token(code), "expires": expires})
        return code

    def pairing_active(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        if self._pairing is not None and self._pairing[1] > now:
            return True
        stored = self._read_pairing()
        return bool(stored) and float(stored.get("expires", 0)) > now

    # ---------------------------------------------------------- pairing, on disk

    def _write_pairing(self, document: dict) -> None:
        # Through the guarded writer: the live code's hash is a secret like any other here.
        self._write_private("pairing.json", json.dumps(document))

    def _read_pairing(self) -> dict | None:
        try:
            raw = self._read_private("pairing.json")
        except OSError:
            return None
        if raw is None:
            return None
        try:
            loaded = json.loads(raw)
        except ValueError:
            return None
        return loaded if isinstance(loaded, dict) else None

    def _claim_pairing_file(self) -> dict | None:
        """Take the live code, atomically, so exactly one caller can have it.

        The rename is the claim. Reading the file and then deleting it would let two processes
        both read it first, which is the cross-process version of the two-step bug the in-memory
        path already had -- and a single-use code that two callers can use is not single-use.
        """
        claimed = ".pairing-claimed-" + secrets.token_hex(8)
        if securedir.SUPPORTED:
            return self._claim_relative(claimed)
        return self._claim_by_path(self.root / claimed)

    def _claim_relative(self, claimed: str) -> dict | None:
        """Claim and read the live pairing record without ever going through a name twice.

        `EM3C-EXTERNAL-0007` found this operating on pathnames while the rest had moved to
        descriptors, which meant the one secret with the shortest life and the highest value was
        the one still reachable by replacing a path.
        """
        fd = self._fd()
        if not securedir.claim_secret(fd, "pairing.json", claimed):
            return None                              # somebody else got there first, or none
        try:
            deadline = time.monotonic() + 2.0
            while True:
                try:
                    raw = securedir.read_secret(fd, claimed)
                except securedir.UnverifiableState:
                    if time.monotonic() >= deadline:
                        return None
                    time.sleep(0.02)
                    continue
                if raw is None:
                    return None
                try:
                    return json.loads(raw)
                except ValueError:
                    return None
        finally:
            securedir.remove_secret(fd, claimed)

    def _claim_by_path(self, claimed) -> dict | None:
        try:
            os.rename(self._pairing_path, claimed)
        except OSError:
            return None                              # somebody else got there first, or none
        # The rename decided the winner. Reading what was won is a separate problem, and on
        # Windows a file that has just been renamed can briefly refuse to open -- another handle,
        # an indexer, a scanner. Giving up there threw away a code this caller had already claimed
        # and consumed it for everyone, which showed up as a race where NOBODY paired. Retry
        # briefly: the claim is already ours, so there is nothing to race against any more.
        try:
            deadline = time.monotonic() + 2.0
            while True:
                try:
                    return json.loads(claimed.read_text(encoding="utf-8"))
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.02)
                except ValueError:
                    return None                      # present and meaningless: not a pairing
        except OSError:
            return None
        finally:
            try:
                os.unlink(claimed)
            except OSError:
                pass

    def withdraw_pairing(self) -> bool:
        """Take back a code that has not been used yet. True when there was one to take back.

        An invitation is handed over out of band -- read aloud, pasted into a chat, photographed
        off a screen -- and any of those can go to the wrong person, or simply go further than
        intended. Until now the only way to deal with that was to issue another one, which
        replaces the first; that works but requires knowing it works, and leaves an operator who
        just wants the outstanding one dead with nothing to do about it.

        The same claim-and-clear the redemption uses, so a withdrawal and a redemption racing
        each other cannot both win.
        """
        with self._pairing_lock:
            self._pairing = None
            return self._claim_pairing_file() is not None

    def redeem_pairing(self, presented: str, client_name: str = "",
                       now: float | None = None) -> str:
        """Exchange a valid code for a token. The code is consumed whether or not it matched.

        Consuming on failure is what stops a wrong guess being cheap: an attacker gets one attempt
        per code the operator issues, in person, at the machine.
        """
        now = time.time() if now is None else now
        # Checked before the attempt is even looked at, so a locked-out caller learns nothing
        # about whether a pairing is live.
        # P2-A (EM3C-STATEDIR-DECISION-0001): nothing here depends on where the attempt came
        # from. The per-origin counter this replaces keyed on the immediate TCP peer, which behind
        # a reverse proxy is the proxy -- so one client shared, and could exhaust, everyone's
        # allowance. Trusting a forwarding header instead would mean trusting whoever can set one.
        #
        # Three limits, none of them an address. Every attempt costs budget whether it succeeds or
        # not; failures additionally accumulate towards a lockout; and a code is consumed by one
        # attempt, so a wrong guess spends that code and nobody else's.
        try:
            self._admission.spend(now)
            self._throttle.check(now)
        except Locked as exc:
            raise PairingError(str(exc)) from None

        # Claim the code and clear it in ONE critical section. Reading it and clearing it as two
        # steps lets two concurrent attempts both see the same live code, which would make a
        # single-use code usable twice. The lock covers threads; the rename below covers
        # processes, since the CLI really does run these in different ones.
        # The disk claim is the ONLY authority, for every caller including the one that issued
        # the code. An earlier version preferred an in-memory copy when it had one, which meant
        # the issuing process could still redeem after another process had won the rename: one
        # code, two tokens. The in-memory copy is cleared here so it cannot be reused, but it is
        # never a substitute for winning the claim.
        with self._pairing_lock:
            self._pairing = None
            on_disk = self._claim_pairing_file()
        pending = None
        expected_hash = ""
        if on_disk is not None:
            pending = ("", float(on_disk.get("expires", 0)))
            expected_hash = str(on_disk.get("code_sha256", ""))

        if pending is None:
            self._throttle.record_failure(now)
            raise PairingError(
                "this gateway is not accepting pairings right now. Run `agentnode gateway pair` "
                "on the server to show a new code."
            )
        _code, expires = pending
        if expires <= now:
            self._throttle.record_failure(now)
            raise PairingError(
                "that pairing code has expired. Run `agentnode gateway pair` on the server for a "
                "new one -- codes last 15 minutes on purpose."
            )
        try:
            presented_norm = normalise_code(presented)
        except PairingError:
            self._throttle.record_failure(now)
            raise
        if not hmac.compare_digest(hash_token(presented_norm), expected_hash):
            self._throttle.record_failure(now)
            raise PairingError(
                "that pairing code does not match. The code can be used once, so ask the server "
                "for a new one with `agentnode gateway pair`."
            )
        # Someone who proved they know the code is not who the throttle guards against.
        self._throttle.record_success(now)
        return self._issue_token(client_name=client_name, now=now)

    # ---------------------------------------------------------------- tokens

    def _read_private(self, name: str) -> str | None:
        if securedir.SUPPORTED:
            return securedir.read_secret(self._fd(), name)
        return self._read_private_by_path(name)

    def _write_private(self, name: str, text: str) -> None:
        if securedir.SUPPORTED:
            securedir.write_secret(self._fd(), name, text)
            return
        self._write_private_by_path(name, text)

    def _read_private_by_path(self, name: str) -> str | None:
        """Read a file inside the gateway directory, through the descriptor that was judged.

        `EM3C-EXTERNAL-0006` found that judging a descriptor and then opening by pathname is still
        two objects: the check applied to the directory that WAS there. Opening relative to the
        held descriptor means the file comes from the directory that was inspected, whatever the
        name points at now.
        """
        self._guard_private()
        path = self.root / name
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def _write_private_by_path(self, name: str, text: str) -> None:
        """The fallback where descriptor-relative work is unavailable.

        Named honestly: this is a pathname operation and offers none of the protection the
        descriptor path does. It exists so a gateway on such a platform still functions, and the
        gateway reports its state as unverifiable rather than claiming otherwise.
        """
        self._guard_private()
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        self._harden(path)

    def close(self) -> None:
        """Give up the held descriptor. Safe to call twice."""
        fd, self._dir_fd = self._dir_fd, None
        if fd is not None:
            try:
                import os as _os

                _os.close(fd)
            except OSError:
                pass

    def _fd(self) -> int:
        """The held descriptor, re-judged, and confirmed to still be what the name refers to.

        Two separate questions. Re-judging catches a change of owner or permissions on the inode
        this gateway is using. The identity comparison catches the directory having been swapped
        for a different one behind the name -- by device and inode, never by comparing strings,
        because one path can name two directories at different moments.
        """
        if self._dir_fd is None:
            raise securedir.UnverifiableState(
                "this gateway has no verified state directory, so it will not touch its secrets"
            )
        securedir.verify_still_private(self._dir_fd, self.root)
        if not securedir.same_object(self._dir_fd, self.root):
            raise securedir.InsecureState(
                f"{self.root} no longer refers to the directory this gateway opened. Something "
                "replaced it while the gateway was running, and nothing was read or written."
            )
        return self._dir_fd

    def _guard_private(self):
        """Refuse to touch the token file while anyone else can read the directory.

        `EM3C-EXTERNAL-0002` found the service-level checks were in the wrong place: they covered
        requests, and `agentnode gateway clients` and `gateway revoke` read and write this file
        without going through a request at all. The chokepoint is the file, so the check is here.

        `EM3C-EXTERNAL-0004` then found that checking a path and opening it are two operations on
        two possibly-different objects. The directory is opened once and the OPEN DESCRIPTOR is
        judged, so what was inspected and what is about to be used are the same thing.
        """
        import os as _os

        from agentnode_sdk.gateway.statedir import InsecureStateDirectory, inspect_fd

        if _os.name != "posix":
            return
        fd = _os.open(str(self.root), _os.O_RDONLY)
        try:
            verdict = inspect_fd(fd, str(self.root))
            if not verdict.ok:
                raise InsecureStateDirectory(
                    verdict.reason + "\n\nNothing was read or written. To fix it:\n  "
                    + verdict.remedy
                )
            return _os.dup(fd)
        finally:
            _os.close(fd)

    def _read_tokens(self) -> dict[str, dict]:
        try:
            raw = self._read_private("tokens.json")
        except OSError:
            return {}
        if raw is None:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            return {}

    def _write_tokens(self, tokens: dict[str, dict]) -> None:
        self._write_private("tokens.json", json.dumps(tokens, indent=2, sort_keys=True))

    def set_client_allowance(self, token: str, allowance) -> bool:
        """Record what a client may reach, against its token.

        `None` means unrestricted at the client scope; a (possibly empty) list of hosts is a
        ceiling that the operator above still narrows further. Stored against the token hash, so
        it is bound to an authenticated identity rather than to anything a job carries.
        """
        tokens = self._read_tokens()
        token_hash = hash_token(token)
        if token_hash not in tokens:
            return False
        tokens[token_hash]["allowance"] = (
            None if allowance is None else sorted({str(h) for h in allowance})
        )
        self._write_tokens(tokens)
        return True

    def client_allowance(self, token: str):
        """The recorded allowance, or None when this client has no ceiling of its own."""
        token_hash = hash_token(token)
        entry = self._read_tokens().get(token_hash) or {}
        return entry.get("allowance")

    def _issue_token(self, client_name: str = "", now: float | None = None) -> str:
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(32)
        tokens = self._read_tokens()
        # Only the hash is stored. A leaked token file must not hand over working credentials.
        tokens[hash_token(token)] = {
            "client_name": str(client_name or "")[:64],
            "issued_at": now,
            # A client's identity is not its credential. The token can be replaced; who the
            # client IS must survive that, or rotating a token would orphan the client's own
            # runs -- which is what happened the first time this was written.
            "client_id": secrets.token_hex(8),
        }
        self._write_tokens(tokens)
        return token

    def client_id_for(self, token: str) -> str | None:
        """Who is holding this token, or None if this gateway did not issue it.

        Falls back to the token hash for entries issued before client ids existed, so an existing
        gateway directory keeps working rather than quietly losing every client on upgrade.
        """
        digest = hash_token(token)
        entry = self._read_tokens().get(digest)
        if entry is None:
            return None
        return str(entry.get("client_id") or digest)

    def token_secret(self, token: str) -> bytes | None:
        """The HMAC key for a token, or None when the token is not one this gateway issued."""
        token_hash = hash_token(token)
        if token_hash not in self._read_tokens():
            return None
        return hashlib.sha256(f"em3c-sig\n{token}".encode()).digest()

    def rotate_token(self, token: str, now: float | None = None) -> str | None:
        """Issue a replacement and retire the old one in a single write.

        Rotation exists so a client that suspects exposure does not have to be re-paired by hand
        at the machine. What the client was allowed to reach travels with it: rotation is meant to
        change the secret and nothing else, and silently widening or narrowing a client's reach
        while replacing its credential would be a second, invisible change.

        Returns None when the token is not one this gateway issued -- the caller says so; this
        does not invent a client.
        """
        tokens = self._read_tokens()
        old_hash = hash_token(token)
        previous = tokens.get(old_hash)
        if previous is None:
            return None
        now = time.time() if now is None else now
        replacement = secrets.token_urlsafe(32)
        entry = dict(previous)          # client_id travels with it: same client, new secret
        entry["issued_at"] = now
        entry["rotated_from"] = old_hash[:12]      # enough to correlate, not enough to reverse
        tokens[hash_token(replacement)] = entry
        # Removed in the same write as the replacement is added, so there is never a moment on
        # disk where both work and never one where neither does.
        del tokens[old_hash]
        self._write_tokens(tokens)
        return replacement

    def revoke(self, token: str) -> bool:
        tokens = self._read_tokens()
        token_hash = hash_token(token)
        if token_hash not in tokens:
            return False
        del tokens[token_hash]
        self._write_tokens(tokens)
        return True

    def paired_clients(self) -> list[dict]:
        return sorted(self._read_tokens().values(), key=lambda t: t.get("issued_at", 0))


def client_token_secret(token: str) -> bytes:
    """The client's side of the same derivation, so both sign with the same key."""
    return hashlib.sha256(f"em3c-sig\n{token}".encode()).digest()
