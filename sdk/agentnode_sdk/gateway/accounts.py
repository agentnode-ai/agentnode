"""Who the customer is, as distinct from what they are holding.

Until this existed there was no customer. There were devices: a paired laptop, an AI connection,
a browser session, each with its own credential and its own counters, and nothing above them. That
is workable for one person running their own gateway and it is not workable for a service, for
three reasons that are separate and all of them blocking:

* **Nothing could be said about a customer.** Ceilings, suspension, billing and deletion are all
  things you do to a customer, and a customer that is only ever "this token" means doing them once
  per credential and getting it wrong the first time somebody pairs a second machine.
* **Every device could see every device.** `devices.list` returned what the gateway held, not what
  the caller owned, and `devices.revoke` resolved its target by scanning every credential on the
  machine. One customer could enumerate another's devices and withdraw them.
* **A second person could not be added at all** without those two becoming a breach rather than an
  untidiness.

## The six things that are not each other

    operator        whoever runs this gateway. Not an account, and no account becomes one.
    account         the customer. Owns devices, carries ceilings, can be suspended as a unit.
    person          somebody who signs in. Today a person is represented by the device they
                    paired; there is no separate sign-in, and this file says so rather than
                    implying otherwise.
    device          one paired credential: a laptop, a server, a CLI.
    AI connection   a device whose holder is a model. Same kind of record, different label, and
                    NOT a different permission system -- an AI connection is bounded by being a
                    device in an account, not by being recognised as an AI.
    run             one execution. Owned by the device that submitted it, inside that account.

Only the first two are new here. The point of writing all six down is that the interesting
mistakes are conflations: an account that is really a device, an AI connection that is really an
account, an operator action attributed to a customer.

## Where an account comes from

Redeeming an invitation creates one, unless the invitation names an existing account -- which is
how a customer adds a second machine. A connection set up from inside the console joins the
account of the session that set it up, because that is what "my AI" means.

## What happens to a device that predates this file

It becomes its own account, `solo:<device>`. That is deliberately the restrictive reading. The
alternative -- putting every existing device into one shared account because they happened to be
on one gateway before accounts existed -- would silently create exactly the cross-customer
visibility this module exists to remove, and would do it during an upgrade, where nobody is
looking.

## Suspension

A recorded fact with a reason, applied to an account as a whole. Absent means active, which is
honest: a gateway that has never suspended anybody has no record to read. Unreadable is NOT
active, for the same reason an unreadable kill switch means stopped -- a gateway that cannot tell
whether a customer is suspended is not one to keep taking their work.

Nothing in this module reads or writes the filesystem. It is given a reader and a writer, so the
one place that decides whether this gateway may touch its own state stays the one place.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass

#: Where the accounts live, inside the gateway's own directory.
ACCOUNTS_NAME = "accounts.json"

#: An account is active, or it is suspended with a reason. There is no third state and no state
#: that means "probably fine".
ACTIVE = "active"
SUSPENDED = "suspended"
STATES = (ACTIVE, SUSPENDED)

#: What an account issued by this gateway looks like.
_PREFIX = "acct-"

#: What a device that predates accounts is treated as: its own account, and nobody else's.
SOLO_PREFIX = "solo:"

#: Accepted shapes. Checked rather than assumed, because an account id ends up as a key in
#: counter files and in the meter, and a value that could contain a separator or a path segment
#: would let one account name another's key.
_WELL_FORMED = re.compile(r"^(acct-[0-9a-f]{16}|solo:[0-9a-zA-Z_-]{1,64})$")


class AccountsUnreadable(OSError):
    """The record of who is suspended cannot be read, so nobody is assumed to be in good standing.

    Its own type because the caller must tell it from an absent file. Absent means this gateway
    has never suspended anybody. Unreadable means it may have, and cannot see who.
    """


class NoSuchAccount(KeyError):
    """Named an account this gateway does not have."""


def solo_account_for(device_id: str) -> str:
    """The account a device belongs to when nothing recorded one.

    One device, one account. See the module docstring for why this is not "one shared account".
    """
    return SOLO_PREFIX + str(device_id or "")


def new_account_id() -> str:
    return _PREFIX + secrets.token_hex(8)


def well_formed(account_id: str) -> bool:
    return bool(_WELL_FORMED.match(str(account_id or "")))


@dataclass(frozen=True)
class Account:
    """One customer. Everything here is something the operator or the gateway decided."""

    account_id: str
    name: str = ""
    created_at: float = 0.0
    state: str = ACTIVE
    #: The operator's own words, shown to the account. Empty while active.
    suspended_because: str = ""
    suspended_at: float = 0.0
    suspended_by: str = ""

    @property
    def active(self) -> bool:
        return self.state == ACTIVE

    def as_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "name": self.name,
            "created_at": self.created_at,
            "state": self.state,
            "suspended_because": self.suspended_because,
            "suspended_at": self.suspended_at,
            "suspended_by": self.suspended_by,
        }


def _account_from(account_id: str, raw: dict) -> Account:
    state = str(raw.get("state") or ACTIVE)
    return Account(
        account_id=str(account_id),
        name=str(raw.get("name") or "")[:64],
        created_at=float(raw.get("created_at") or 0.0),
        # An unrecognised state is read as suspended, not as active. A file written by a newer
        # build, or damaged in a way that survives JSON, must not widen what an account may do.
        state=state if state in STATES else SUSPENDED,
        suspended_because=str(raw.get("suspended_because") or "")[:400],
        suspended_at=float(raw.get("suspended_at") or 0.0),
        suspended_by=str(raw.get("suspended_by") or "")[:64],
    )


class Accounts:
    """The customers this gateway holds, over whatever storage it was given.

    `read(name)` returns the text or None; `write(name, text)` stores it. Both come from the
    gateway state, so account data goes through the same descriptor that every other private file
    does and is refused in the same circumstances.
    """

    def __init__(self, read, write) -> None:
        self._read_raw = read
        self._write_raw = write

    # ------------------------------------------------------------------ the file

    def _load(self) -> dict:
        try:
            raw = self._read_raw(ACCOUNTS_NAME)
        except OSError as exc:
            raise AccountsUnreadable(
                "this gateway cannot read which of its accounts are suspended (%s), so it is "
                "not taking their work until it can." % str(exc)[:120]) from exc
        if raw is None:
            return {}
        try:
            body = json.loads(raw)
        except ValueError as exc:
            raise AccountsUnreadable(
                "the record of this gateway's accounts is not readable as JSON (%s). It will not "
                "take work until that is fixed: carrying on would mean treating a suspended "
                "customer as one in good standing." % str(exc)[:120]) from exc
        if not isinstance(body, dict):
            raise AccountsUnreadable(
                "the record of this gateway's accounts is not an object, so it cannot be read as "
                "a list of accounts.")
        return body

    def _store(self, body: dict) -> None:
        self._write_raw(ACCOUNTS_NAME, json.dumps(body, indent=2, sort_keys=True))

    # ------------------------------------------------------------------ reading

    def all(self) -> list[Account]:
        """Every account with a record. Solo accounts appear only once something was recorded."""
        body = self._load()
        return sorted((_account_from(k, v) for k, v in body.items() if isinstance(v, dict)),
                      key=lambda a: (a.created_at, a.account_id))

    def get(self, account_id: str) -> Account:
        """What this gateway knows about an account, including one it has never recorded.

        An account with no record is ACTIVE. That is not a permissive fallback dressed up: the
        file is the record of *suspensions*, and a gateway that has suspended nobody has nothing
        to write. The permissive fallback would be reading an UNREADABLE file as no suspensions,
        and `_load` raises rather than doing that.
        """
        wanted = str(account_id or "")
        if not well_formed(wanted):
            raise NoSuchAccount(wanted)
        raw = self._load().get(wanted)
        if isinstance(raw, dict):
            return _account_from(wanted, raw)
        return Account(account_id=wanted, state=ACTIVE)

    def recorded(self, account_id: str) -> bool:
        return isinstance(self._load().get(str(account_id)), dict)

    # ------------------------------------------------------------------ writing

    def create(self, name: str = "", now: float | None = None,
               account_id: str = "") -> Account:
        """Bring a new customer into existence. Called when an invitation is redeemed."""
        at = time.time() if now is None else now
        wanted = str(account_id or new_account_id())
        if not well_formed(wanted):
            raise NoSuchAccount(wanted)
        body = self._load()
        if wanted in body:
            return _account_from(wanted, body[wanted])
        account = Account(account_id=wanted, name=str(name or "")[:64], created_at=at)
        body[wanted] = account.as_dict()
        self._store(body)
        return account

    def ensure(self, account_id: str, name: str = "", now: float | None = None) -> Account:
        """Record an account that already exists implicitly, so something can be written about it.

        A solo account has no record until somebody suspends it or sets a ceiling on it. This is
        how one comes to have one, without that being mistaken for creating a new customer.
        """
        return self.create(name=name, now=now, account_id=account_id)

    def suspend(self, account_id: str, because: str, by: str = "",
                now: float | None = None) -> Account:
        """Stop an account. Its work is refused from the next request, with these words."""
        at = time.time() if now is None else now
        wanted = str(account_id)
        if not well_formed(wanted):
            raise NoSuchAccount(wanted)
        if not str(because or "").strip():
            # A suspension with no reason is one the account cannot act on and the operator
            # cannot justify later. It is refused at the point of suspending rather than
            # discovered by the customer.
            raise ValueError("a suspension has to say why: the account is shown this.")
        body = self._load()
        existing = body.get(wanted) if isinstance(body.get(wanted), dict) else {}
        account = Account(
            account_id=wanted,
            name=str(existing.get("name") or "")[:64],
            created_at=float(existing.get("created_at") or at),
            state=SUSPENDED,
            suspended_because=str(because)[:400],
            suspended_at=at,
            suspended_by=str(by or "")[:64],
        )
        body[wanted] = account.as_dict()
        self._store(body)
        return account

    def restore(self, account_id: str, now: float | None = None) -> Account:
        """Let an account work again. Deliberate, and never a side effect of anything else."""
        at = time.time() if now is None else now
        wanted = str(account_id)
        if not well_formed(wanted):
            raise NoSuchAccount(wanted)
        body = self._load()
        existing = body.get(wanted) if isinstance(body.get(wanted), dict) else {}
        account = Account(
            account_id=wanted,
            name=str(existing.get("name") or "")[:64],
            created_at=float(existing.get("created_at") or at),
            state=ACTIVE,
        )
        body[wanted] = account.as_dict()
        self._store(body)
        return account

    def forget(self, account_id: str) -> bool:
        """Remove the record entirely. Used by deletion; not a way to lift a suspension.

        Deliberately distinct from `restore`: forgetting a SUSPENDED account would silently
        reactivate it, so a caller that means "let them work again" has to say so.
        """
        wanted = str(account_id)
        body = self._load()
        if wanted not in body:
            return False
        del body[wanted]
        self._store(body)
        return True


class Suspended(Exception):
    """This account is suspended. Carries the operator's own words for it."""

    def __init__(self, account_id: str, because: str) -> None:
        super().__init__(because)
        self.account_id = account_id
        self.because = because
