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
from dataclasses import dataclass
from pathlib import Path

#: A pairing code is typed by a human, so it avoids characters that look alike in most fonts.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_GROUPS, CODE_GROUP_LEN = 3, 4
PAIRING_TTL_SECONDS = 15 * 60


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
        if self._identity_path.is_file():
            try:
                data = json.loads(self._identity_path.read_text(encoding="utf-8"))
                gid = str(data["gateway_id"])
            except (OSError, ValueError, KeyError) as exc:
                raise PairingError(
                    f"the gateway's identity file is unreadable ({exc}). Remove "
                    f"{self._identity_path} and start the gateway again to issue a new identity; "
                    "every paired client will have to pair again."
                ) from exc
        else:
            gid = secrets.token_hex(16)
            self._identity_path.write_text(
                json.dumps({"gateway_id": gid}, indent=2), encoding="utf-8"
            )
            self._harden(self._identity_path)
        return GatewayIdentity(gateway_id=gid, version=self.version)

    # ---------------------------------------------------------------- pairing

    def start_pairing(self, now: float | None = None) -> str:
        """Issue a pairing code. Only one is live at a time: a second call replaces the first.

        Replacing rather than accumulating is deliberate. Several live codes would each be a way
        in, and a person who pressed the button twice would have no idea how many were valid.
        """
        now = time.time() if now is None else now
        code = new_pairing_code()
        self._pairing = (code, now + PAIRING_TTL_SECONDS)
        return code

    def pairing_active(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return self._pairing is not None and self._pairing[1] > now

    def redeem_pairing(self, presented: str, client_name: str = "",
                       now: float | None = None) -> str:
        """Exchange a valid code for a token. The code is consumed whether or not it matched.

        Consuming on failure is what stops a wrong guess being cheap: an attacker gets one attempt
        per code the operator issues, in person, at the machine.
        """
        now = time.time() if now is None else now
        if self._pairing is None:
            raise PairingError(
                "this gateway is not accepting pairings right now. Run `agentnode gateway pair` "
                "on the server to show a new code."
            )
        code, expires = self._pairing
        self._pairing = None
        if expires <= now:
            raise PairingError(
                "that pairing code has expired. Run `agentnode gateway pair` on the server for a "
                "new one -- codes last 15 minutes on purpose."
            )
        try:
            presented_norm = normalise_code(presented)
        except PairingError:
            raise
        if not hmac.compare_digest(presented_norm, code):
            raise PairingError(
                "that pairing code does not match. The code can be used once, so ask the server "
                "for a new one with `agentnode gateway pair`."
            )
        return self._issue_token(client_name=client_name, now=now)

    # ---------------------------------------------------------------- tokens

    def _read_tokens(self) -> dict[str, dict]:
        if not self._tokens_path.is_file():
            return {}
        try:
            return json.loads(self._tokens_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write_tokens(self, tokens: dict[str, dict]) -> None:
        self._tokens_path.write_text(json.dumps(tokens, indent=2, sort_keys=True),
                                     encoding="utf-8")
        self._harden(self._tokens_path)

    def _issue_token(self, client_name: str = "", now: float | None = None) -> str:
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(32)
        tokens = self._read_tokens()
        # Only the hash is stored. A leaked token file must not hand over working credentials.
        tokens[hashlib.sha256(token.encode("utf-8")).hexdigest()] = {
            "client_name": str(client_name or "")[:64],
            "issued_at": now,
        }
        self._write_tokens(tokens)
        return token

    def token_secret(self, token: str) -> bytes | None:
        """The HMAC key for a token, or None when the token is not one this gateway issued."""
        token_hash = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()
        if token_hash not in self._read_tokens():
            return None
        return hashlib.sha256(f"em3c-sig\n{token}".encode()).digest()

    def revoke(self, token: str) -> bool:
        tokens = self._read_tokens()
        token_hash = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()
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
