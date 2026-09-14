"""How a browser holds its access, given that a browser cannot be trusted to hold a secret.

Every other client of this gateway keeps a device token and presents it. A browser cannot: any
script on the page can read anything the page can read, so a token in `localStorage` or
`sessionStorage` is a token that one cross-site-scripting mistake hands to somebody else, for as
long as it stays valid. The usual answer -- "we are careful about XSS" -- is a promise about
future code, which is not a security property.

So a browser gets no token at all. It gets a session: a random identifier this gateway keeps the
hash of, handed to the browser in a cookie the page's own JavaScript cannot read. A script that
runs on the page can still ACT as the person, because the browser will attach the cookie; what it
cannot do is take the credential somewhere else, or keep using it after the person has ended the
session. That is a smaller blast radius and an honest one.

Three things follow from that, and each is here rather than in the page:

* **Nothing readable.** The cookie is `HttpOnly`, so `document.cookie` does not contain it.
* **Nothing forgeable from another site.** `SameSite=Strict` means another origin's requests
  carry no cookie at all, and a CSRF token -- kept in memory by the page, never in storage -- is
  required for anything that changes state. Either alone would be thin; together a cross-site
  request has neither the cookie nor the token.
* **Nothing that outlives the person's decision.** Sessions expire, can be listed, and can be
  ended one at a time or all at once, server-side, taking effect on the next request.

What is stored is only ever a HASH. A stolen `sessions.json` contains nothing that can be
presented to this gateway -- the same rule the device tokens already follow, for the same reason.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time

#: How long a session lasts without being used. Long enough to be useful across a working day,
#: short enough that a browser left open on a shared machine is not a standing invitation.
GOOD_FOR_SECONDS = 12 * 60 * 60
#: How long it lasts at all, however much it is used. A session nobody ever ends still ends.
AT_MOST_SECONDS = 7 * 24 * 60 * 60
#: How many a single account may have open. A person has a laptop and a phone, not four hundred.
AT_ONCE = 24


def fingerprint(value: str) -> str:
    """How a session or a CSRF token is named without being stored."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


class TooManySessions(Exception):
    """An account already has as many open sessions as it may."""


class Sessions:
    """The sessions this gateway has open, and the only thing that decides whether one is valid."""

    def __init__(self, root, clock=time.time) -> None:
        self._path = os.path.join(str(root), "sessions.json")
        self._clock = clock
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ opening one

    def open(self, client_id: str, label: str = "") -> tuple:
        """Start a session for a device. Returns `(session_id, csrf)` -- once, and never again.

        Both are generated here and neither is stored. What is written down is their hashes, so
        this file can be read by somebody who should not have it without handing them a way in.
        """
        session_id = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            kept = self._read()
            mine = [k for k, v in kept.items() if v.get("client_id") == client_id]
            if len(mine) >= AT_ONCE:
                raise TooManySessions(
                    "this account already has %d sessions open, which is as many as it may have"
                    % AT_ONCE)
            kept[fingerprint(session_id)] = {
                "client_id": str(client_id),
                "csrf": fingerprint(csrf),
                "label": str(label or ""),
                "opened_at": now,
                "last_used": now,
                "ends_at": now + AT_MOST_SECONDS,
            }
            self._write(kept)
        return session_id, csrf

    # ------------------------------------------------------------------ using one

    def whose(self, session_id: str) -> dict | None:
        """Who this session belongs to, or None. Expired is None, and is removed on the way past.

        Touches `last_used`, because a session that is being used is not idle -- and idleness is
        what the shorter of the two expiries is about.
        """
        if not session_id:
            return None
        named = fingerprint(session_id)
        now = self._clock()
        with self._lock:
            kept = self._read()
            found = kept.get(named)
            if found is None:
                return None
            if self._is_over(found, now):
                del kept[named]
                self._write(kept)
                return None
            found["last_used"] = now
            kept[named] = found
            self._write(kept)
            return dict(found, session=named)

    def csrf_matches(self, session_id: str, presented: str) -> bool:
        """Constant-time, and false for anything missing rather than raising."""
        found = self.whose(session_id)
        if not found or not presented:
            return False
        return hmac.compare_digest(str(found.get("csrf", "")), fingerprint(presented))

    # ------------------------------------------------------------------ ending one

    def end(self, session_id: str) -> bool:
        """End the session somebody is holding. What a person means by "log out"."""
        return self._forget(fingerprint(session_id))

    def end_named(self, named: str) -> bool:
        """End a session from the list, by the name the list shows. Never needs the session id.

        Whoever ends a session is looking at a list of them -- their other laptop, a machine they
        have lost. They have the name this gateway assigned and they do not have the identifier,
        which is exactly the right way round: a revoke that needed the identifier could only be
        performed by the session being revoked.
        """
        return self._forget(str(named))

    def end_every(self, client_id: str) -> int:
        """Every session this device has. What withdrawing a device has to imply."""
        with self._lock:
            kept = self._read()
            going = [k for k, v in kept.items() if v.get("client_id") == str(client_id)]
            for k in going:
                del kept[k]
            if going:
                self._write(kept)
            return len(going)

    def _forget(self, named: str) -> bool:
        with self._lock:
            kept = self._read()
            if kept.pop(named, None) is None:
                return False
            self._write(kept)
            return True

    # ------------------------------------------------------------------ looking at them

    def belonging_to(self, client_id: str) -> list:
        """What a person is shown. No identifier, no CSRF token, nothing presentable."""
        now = self._clock()
        with self._lock:
            kept = self._read()
        out = []
        for named, found in sorted(kept.items(), key=lambda kv: kv[1].get("opened_at", 0)):
            if found.get("client_id") != str(client_id) or self._is_over(found, now):
                continue
            out.append({
                "session": named,
                "label": found.get("label", ""),
                "opened_at": int(found.get("opened_at", 0)),
                "last_used": int(found.get("last_used", 0)),
                "ends_at": int(found.get("ends_at", 0)),
            })
        return out

    # ------------------------------------------------------------------ the file

    def _is_over(self, found: dict, now: float) -> bool:
        idle = now - float(found.get("last_used", 0)) > GOOD_FOR_SECONDS
        return bool(idle or now >= float(found.get("ends_at", 0)))

    def _read(self) -> dict:
        try:
            with open(self._path, encoding="utf-8") as fh:
                kept = json.load(fh)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            # Unreadable is not empty. Reading it as "nobody is signed in" would end every
            # session on a transient disk error, and reading it as "everybody is" is worse.
            raise
        return kept if isinstance(kept, dict) else {}

    def _write(self, kept: dict) -> None:
        near = self._path + ".new"
        with open(near, "w", encoding="utf-8") as fh:
            json.dump(kept, fh, sort_keys=True)
        try:
            os.chmod(near, 0o600)
        except OSError:                                       # pragma: no cover - platform
            pass
        os.replace(near, self._path)
