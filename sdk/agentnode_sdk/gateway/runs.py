"""Every run this gateway holds, and the same runs indexed by WHO owns them.

## Why this exists

`_a_run_of_this_caller` used to look a run up in one mapping of every run on the gateway and
reject it afterwards if it belonged to somebody else. The ANSWER was already identical for a
foreign run and a run that never existed. The WORK was not: a foreign run was found and then
rejected, an absent one was not found at all, so the branch structure depended on whether
another account's object existed. `EXISTENCE-ISOLATION-DECISION-0001` chose to remove that at
its source rather than to weaken the criterion that caught it.

So a customer's path asks `owned_by(account_id, client_id).get(run_id)`, which is one dictionary
miss whether the identifier belongs to another account, to another device of the same account,
or to nobody at all. There is no "found, then refused".

## Why it is a class rather than two dicts side by side

Because the finding against the decision -- `F1-INDEX-CONSISTENCY` -- is the right worry. An
index maintained by whoever remembers to maintain it drifts, and the way it drifts is that a
customer's OWN run becomes unreachable. Every mutation goes through here, there is no path that
writes one mapping without the other, and `everything_matches()` rebuilds the index from the
contents so a test can assert they agree after any sequence of operations.

## What it is not

It is not an access decision. It answers "which runs belong to this owner"; whether this caller
IS that owner was established before anything here was called, in the one place that decides
that. A mapping that started refusing things would be a second decision point, which is the
arrangement this whole layer exists to end.
"""
from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterator, Mapping, MutableMapping
from typing import Any

#: Every caller-supplied identifier is folded into a buffer of exactly this many bytes before it
#: is hashed, so a four-character identifier and a four-kilobyte one cost the same to look up.
#: Identifier LENGTH stops being a variable an attacker can move.
WIDTH = 64

#: The folded key's width. Sixteen bytes is far more than the number of runs any gateway holds,
#: and a collision would only ever mean a lookup missing -- never one account reaching another's
#: run, because the namespace it is looked up in is chosen before the key is computed.
KEY_WIDTH = 16


class _Namespace(Mapping):
    """One owner's runs, addressed by the identifiers their caller actually sends.

    The mapping inside is keyed by a FOLDED digest rather than by the caller's string. Three
    things follow, and `TIMING-PROTOCOL-V2-DECISION-0001` chose the design for the third:

    * every lookup costs the same regardless of how long the identifier was;
    * the fold is bound to the OWNER, so the same string produces different keys in different
      accounts and bucket structure in one namespace says nothing about another's;
    * an attacker cannot choose where in the table their probe lands, because the fold is keyed
      with a secret that exists only in this process's memory.

    What it does NOT do is make the work constant. A hit and a miss still differ. It removes
    attacker CONTROL over that and removes width as a variable; both are structural, and neither
    is a timing claim. See `docs/review/TIMING-PROTOCOL-V2.md`.
    """

    __slots__ = ("_key", "_runs")

    def __init__(self, key: bytes, runs: dict) -> None:
        self._key = key
        self._runs = runs

    def _fold(self, run_id) -> bytes:
        raw = str(run_id).encode("utf-8", "surrogatepass")[:WIDTH].ljust(WIDTH, b"\0")
        return hashlib.blake2b(raw, key=self._key, digest_size=KEY_WIDTH).digest()

    def get(self, run_id, default=None):
        return self._runs.get(self._fold(run_id), default)

    def __getitem__(self, run_id):
        return self._runs[self._fold(run_id)]

    def __contains__(self, run_id) -> bool:
        return self._fold(run_id) in self._runs

    def __iter__(self) -> Iterator:
        return iter(self._runs)

    def __len__(self) -> int:
        return len(self._runs)

    def __eq__(self, other) -> bool:
        # So that an owner with nothing compares equal to `{}`, which is how a caller asks
        # "is there anything here" without caring how it is keyed.
        if isinstance(other, _Namespace):
            return self._runs == other._runs
        return len(self._runs) == 0 and isinstance(other, Mapping) and len(other) == 0

    def __hash__(self):                                       # pragma: no cover - not a key
        return id(self)


class Runs(MutableMapping):
    """A mapping of run id to record, with a second view keyed by who owns it.

    Behaves as the plain dictionary it replaced -- `runs[x]`, `runs.get(x)`, `runs.values()`,
    `len(runs)`, iteration, `setdefault`, `pop`, `del` -- so the paths that legitimately hold
    every run (the operator's, the gateway's own bookkeeping, restart recovery) are unchanged.
    """

    __slots__ = ("_all", "_by_owner", "_secret", "_owner_keys")

    def __init__(self) -> None:
        self._all: dict[str, Any] = {}
        #: (account_id, client_id) -> {folded key: record}. A run whose owner is not fully
        #: recorded is in NO owner's namespace: it belongs to nobody, which is what the
        #: dispatcher's fail-closed ownership test already says about it.
        self._by_owner: dict[tuple[str, str], dict[bytes, Any]] = {}
        #: IN MEMORY, for the life of this process, and never written down. It namespaces the
        #: fold; it authenticates nothing. So it has no lifecycle, no file, no permissions and
        #: nothing to leak -- `TIMING-PROTOCOL-V2-DECISION-0001` finding F1-HOT-PATH-AND-SECRET
        #: asked what its lifecycle is, and the answer is that losing it is what a restart is.
        #: The index it namespaces is rebuilt at startup anyway.
        self._secret = secrets.token_bytes(32)
        #: Derived once per owner, so a lookup costs ONE fold rather than two.
        self._owner_keys: dict[tuple[str, str], bytes] = {}

    # ------------------------------------------------------------------ the mapping

    def __getitem__(self, run_id: str):
        return self._all[str(run_id)]

    def __setitem__(self, run_id: str, record) -> None:
        run_id = str(run_id)
        was = self._all.get(run_id)
        if was is not None:
            self._forget(run_id, was)
        self._all[run_id] = record
        self._remember(run_id, record)

    def __delitem__(self, run_id: str) -> None:
        run_id = str(run_id)
        record = self._all.pop(run_id)
        self._forget(run_id, record)

    def __iter__(self) -> Iterator[str]:
        return iter(self._all)

    def __len__(self) -> int:
        return len(self._all)

    def __contains__(self, run_id) -> bool:
        return str(run_id) in self._all

    # ------------------------------------------------------------------ the owner's view

    def owned_by(self, account_id: str, client_id: str) -> _Namespace:
        """The runs of ONE owner. A caller asking about anything else gets a miss, not a refusal.

        Returns a namespace over the live mapping when there is one, and a shared EMPTY namespace
        when there is not -- so an owner with no runs costs what an owner nobody has heard of
        costs, and both fold the identifier before probing rather than short-cutting on being
        empty. The result is read, never written: writing into it would put a run in an owner's
        namespace without putting it in the mapping of every run.
        """
        where = (str(account_id), str(client_id))
        # The owner's OWN key either way, including when they have nothing. A shared empty
        # namespace with one fixed key would be cheaper to hand back, and it would also mean the
        # fold stopped being owner-bound for exactly the owners who have nothing -- the same
        # identifier would then fold identically for every one of them. Nothing leaks from that
        # today, because the mapping is empty and no comparison happens. It is refused anyway: a
        # property that holds for most owners is not the property.
        return _Namespace(self._key_for(where), self._by_owner.get(where) or _NOTHING)

    def _key_for(self, where: tuple) -> bytes:
        """This owner's fold key, derived once from the process secret and remembered."""
        key = self._owner_keys.get(where)
        if key is None:
            key = hashlib.blake2b(
                ("\x1f".join(where)).encode("utf-8"), key=self._secret, digest_size=32).digest()
            self._owner_keys[where] = key
        return key

    def owner_of(self, run_id: str) -> tuple:
        """Who a run belongs to, for the paths that legitimately hold every run."""
        record = self._all.get(str(run_id))
        if record is None:
            return ("", "")
        return (str(getattr(record, "owner_account_id", "") or ""),
                str(getattr(record, "owner_client_id", "") or ""))

    def reindex(self, run_id: str) -> None:
        """Say that a record's OWNER changed under this mapping's feet.

        Nothing in this product changes a run's owner after it is created, and if something
        starts to, this is what it must call. It is here because an index that can silently fall
        out of step with a field somebody else mutates is the failure `F1-INDEX-CONSISTENCY`
        names, and a method that has to be called is at least a method a reader can look for.
        """
        run_id = str(run_id)
        record = self._all.get(run_id)
        # Folded PER OWNER: the same identifier has a different key in every namespace, which is
        # the point of binding the fold to the owner. Popping the raw id would find nothing and
        # leave the record in its old namespace -- reachable by the account it no longer belongs
        # to, which is the one outcome this whole file exists to prevent.
        for where, runs in list(self._by_owner.items()):
            runs.pop(_Namespace(self._key_for(where), runs)._fold(run_id), None)
            if not runs:
                del self._by_owner[where]
        if record is not None:
            self._remember(run_id, record)

    # ------------------------------------------------------------------ the invariant

    def everything_matches(self) -> bool:
        """Whether the index says exactly what the contents say. For tests to assert."""
        rebuilt: dict[tuple[str, str], dict[bytes, Any]] = {}
        for run_id, record in self._all.items():
            where = self._key(record)
            if where is None:
                continue
            runs = rebuilt.setdefault(where, {})
            runs[_Namespace(self._key_for(where), runs)._fold(run_id)] = record
        held = {k: v for k, v in self._by_owner.items() if v}
        return held == rebuilt

    # ------------------------------------------------------------------ inside

    @staticmethod
    def _key(record):
        account = str(getattr(record, "owner_account_id", "") or "")
        client = str(getattr(record, "owner_client_id", "") or "")
        # BOTH, or neither. A half-owned run belongs to nobody -- the same answer the
        # dispatcher's ownership test gives it, and the same reason: a record that names half an
        # owner is a record from a path that did not finish writing one.
        return (account, client) if account and client else None

    def _remember(self, run_id: str, record) -> None:
        where = self._key(record)
        if where is None:
            return
        runs = self._by_owner.setdefault(where, {})
        runs[_Namespace(self._key_for(where), runs)._fold(run_id)] = record

    def _forget(self, run_id: str, record) -> None:
        where = self._key(record)
        if where is None:
            return
        runs = self._by_owner.get(where)
        if runs is None:
            return
        runs.pop(_Namespace(self._key_for(where), runs)._fold(run_id), None)
        if not runs:
            del self._by_owner[where]


#: One shared empty MAPPING behind every owner that has no runs, so "nobody by that name" costs
#: what "nobody with any runs" costs -- including the fold, which happens either way rather than
#: being skipped because there is nothing to find. The namespace around it still carries that
#: owner's own key. Never written to: a caller that wrote here would corrupt every empty owner
#: at once, and `owned_by` documents that its result is read-only.
_NOTHING: dict = {}
