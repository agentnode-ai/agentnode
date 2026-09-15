"""Invitations to join an account that already exists.

Adding a second machine used to mean asking the operator, who ran `agentnode gateway pair
--account acct-7e7f7df558f58de5`. Two things are wrong with that as a product:

* a customer cannot do it at all, so "add my laptop" is a support request;
* it makes somebody type an account id, and a customer who has to know their account id is a
  customer who has been handed a piece of our plumbing.

So an account can invite its own next device. What makes that safe is not a check somebody
remembers to write: it is that the invitation IS the account. The account comes from the
principal that asked, never from a parameter, so there is no spelling of this call that produces
an invitation into somebody else's account.

## Why this is not the pairing code

`identity.start_pairing` has exactly one live code at a time, deliberately: several live codes
would each be a way in and a person who pressed the button twice would not know how many were
valid. That is right for the operator's invitation, which creates a NEW customer.

It is wrong here. Two customers adding a machine at the same moment would each silently replace
the other's invitation, and the operator's outstanding one as well. These are a different object:
several may be live, each belongs to one account, and withdrawing one belongs to the account that
made it.

## What is stored

The HASH of the code, never the code. A copy of this file is what an attacker with the gateway's
directory gets, and a live invitation sitting in it in plain text would be a working key for as
long as it lasts.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path

from agentnode_sdk.gateway.filelock import ProcessLock

#: Where they live, inside the gateway's own directory.
JOINING_NAME = "joining.json"

#: How long one lasts. Long enough to walk to the other machine, short enough that a code read
#: aloud in a room is not a key to that room next week.
GOOD_FOR_SECONDS = 30 * 60

#: How many an account may have outstanding. A person adds a laptop and a phone, not forty.
AT_MOST_EACH = 5

#: The same human-typeable alphabet the operator's pairing code uses, so a person meets one kind
#: of code rather than two.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
GROUPS, GROUP_LEN = 3, 4

_SHAPE = re.compile(r"^[%s]{%d}(-[%s]{%d}){%d}$"
                    % (_ALPHABET, GROUP_LEN, _ALPHABET, GROUP_LEN, GROUPS - 1))


class TooManyOutstanding(Exception):
    """This account already has as many invitations open as it may."""


def new_code() -> str:
    return "-".join("".join(secrets.choice(_ALPHABET) for _ in range(GROUP_LEN))
                    for _ in range(GROUPS))


def normalise(raw: str) -> str:
    """Accept what a person plausibly types: any case, spaces, missing or extra dashes."""
    cleaned = "".join(ch for ch in str(raw or "").upper() if ch in _ALPHABET)
    if len(cleaned) != GROUPS * GROUP_LEN:
        return ""
    return "-".join(cleaned[i:i + GROUP_LEN] for i in range(0, len(cleaned), GROUP_LEN))


def _fingerprint(code: str) -> str:
    return hashlib.sha256(("joining\n" + str(code)).encode("utf-8")).hexdigest()


def a_name_for(code: str) -> str:
    """What the list shows. Not the code, and not enough of it to guess the rest.

    A person looking at their outstanding invitations needs to tell one from another; anything
    that could be typed back in would make the list itself a way in.
    """
    return _fingerprint(code)[:8]


class Joining:
    """The invitations this gateway is holding, for accounts that already exist."""

    def __init__(self, root, clock=time.time) -> None:
        self.path = Path(root) / JOINING_NAME
        self._clock = clock
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ making one

    def offer(self, account_id: str, by_device: str = "", label: str = "") -> dict:
        """Issue an invitation into this account. Returns the code and when it stops working.

        The code is returned ONCE, here. Nothing stores it and nothing can show it again: a list
        that could re-display an invitation would make the list as good as the invitation.
        """
        now = self._clock()
        code = new_code()
        with self._lock, ProcessLock(self.path):
            kept = self._tidied(self._read(), now)
            mine = [v for v in kept.values() if v.get("account_id") == str(account_id)]
            if len(mine) >= AT_MOST_EACH:
                raise TooManyOutstanding(
                    "this account already has %d invitations open, which is as many as it may. "
                    "Use one, or withdraw one, before making another." % len(mine))
            kept[_fingerprint(code)] = {
                "account_id": str(account_id),
                "by_device": str(by_device or ""),
                "label": str(label or "")[:64],
                "made_at": now,
                "expires_at": now + GOOD_FOR_SECONDS,
            }
            self._write(kept)
        return {"code": code, "name": a_name_for(code),
                "expires_at": int(now + GOOD_FOR_SECONDS)}

    # ------------------------------------------------------------------ using one

    def redeem(self, presented: str) -> str:
        """The account this code joins, or "" if it is not one of ours.

        Claimed and removed in ONE critical section, so two machines racing on one invitation
        cannot both be let in -- the same single-use property the pairing code has, for the same
        reason.
        """
        code = normalise(presented)
        if not code or not _SHAPE.match(code):
            return ""
        now = self._clock()
        with self._lock, ProcessLock(self.path):
            kept = self._tidied(self._read(), now)
            found = kept.pop(_fingerprint(code), None)
            if found is None:
                return ""
            self._write(kept)
            if now >= float(found.get("expires_at", 0)):
                return ""
            return str(found.get("account_id") or "")

    # ------------------------------------------------------------------ looking at them

    def outstanding(self, account_id: str) -> list:
        """What this account has open. Names and times, never anything typeable."""
        now = self._clock()
        with self._lock:
            kept = self._tidied(self._read(), now)
        return sorted(
            ({"invitation": name[:8], "label": found.get("label", ""),
              "made_at": int(found.get("made_at", 0)),
              "expires_at": int(found.get("expires_at", 0))}
             for name, found in kept.items()
             if found.get("account_id") == str(account_id)),
            key=lambda o: o["made_at"])

    def withdraw(self, account_id: str, named: str) -> bool:
        """Take one back before it is used. By the name the list shows, and only your own."""
        now = self._clock()
        with self._lock, ProcessLock(self.path):
            kept = self._tidied(self._read(), now)
            for fingerprint, found in list(kept.items()):
                if fingerprint[:8] != str(named):
                    continue
                if found.get("account_id") != str(account_id):
                    # The same answer as one that does not exist. Telling somebody an invitation
                    # exists but is not theirs tells them it exists.
                    return False
                del kept[fingerprint]
                self._write(kept)
                return True
            return False

    def drop_everything_of(self, account_id: str) -> int:
        """Every invitation this account has open. What deleting a customer has to imply."""
        with self._lock, ProcessLock(self.path):
            kept = self._read()
            going = [k for k, v in kept.items() if v.get("account_id") == str(account_id)]
            for k in going:
                del kept[k]
            if going:
                self._write(kept)
            return len(going)

    def drop_everything_from(self, device_id: str) -> int:
        """Every invitation this DEVICE made. What withdrawing a device has to imply.

        An unspent invitation made by a device that has since been withdrawn is a way back into
        the account it was withdrawn from -- the same shape as an unspent download ticket, and
        the same reason it goes.
        """
        with self._lock, ProcessLock(self.path):
            kept = self._read()
            going = [k for k, v in kept.items() if v.get("by_device") == str(device_id)]
            for k in going:
                del kept[k]
            if going:
                self._write(kept)
            return len(going)

    # ------------------------------------------------------------------ the file

    def _tidied(self, kept: dict, now: float) -> dict:
        return {k: v for k, v in kept.items() if now < float(v.get("expires_at", 0))}

    def _read(self) -> dict:
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return body if isinstance(body, dict) else {}

    def _write(self, body: dict) -> None:
        """Beside and renamed over, with the bounded retry Windows needs. One implementation."""
        from agentnode_sdk.gateway.filelock import atomically

        atomically(self.path, json.dumps(body, sort_keys=True))
