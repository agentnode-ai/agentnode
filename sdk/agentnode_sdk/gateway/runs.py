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

from collections.abc import Iterator, MutableMapping
from typing import Any


class Runs(MutableMapping):
    """A mapping of run id to record, with a second view keyed by who owns it.

    Behaves as the plain dictionary it replaced -- `runs[x]`, `runs.get(x)`, `runs.values()`,
    `len(runs)`, iteration, `setdefault`, `pop`, `del` -- so the paths that legitimately hold
    every run (the operator's, the gateway's own bookkeeping, restart recovery) are unchanged.
    """

    __slots__ = ("_all", "_by_owner")

    def __init__(self) -> None:
        self._all: dict[str, Any] = {}
        #: (account_id, client_id) -> {run_id: record}. A run whose owner is not fully recorded
        #: is in NO owner's namespace: it belongs to nobody, which is what the dispatcher's
        #: fail-closed ownership test already says about it.
        self._by_owner: dict[tuple[str, str], dict[str, Any]] = {}

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

    def owned_by(self, account_id: str, client_id: str) -> dict:
        """The runs of ONE owner. A caller asking about anything else gets a miss, not a refusal.

        Returns the live mapping when there is one and a shared empty mapping when there is not,
        so an owner with no runs costs the same as an owner with some. The result is read, never
        written: writing into it would put a run in an owner's namespace without putting it in
        the mapping of every run.
        """
        return self._by_owner.get((str(account_id), str(client_id)), _NOTHING)

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
        for where in self._by_owner.values():
            where.pop(run_id, None)
        if record is not None:
            self._remember(run_id, record)

    # ------------------------------------------------------------------ the invariant

    def everything_matches(self) -> bool:
        """Whether the index says exactly what the contents say. For tests to assert."""
        rebuilt: dict[tuple[str, str], dict[str, Any]] = {}
        for run_id, record in self._all.items():
            key = self._key(record)
            if key is None:
                continue
            rebuilt.setdefault(key, {})[run_id] = record
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
        key = self._key(record)
        if key is not None:
            self._by_owner.setdefault(key, {})[run_id] = record

    def _forget(self, run_id: str, record) -> None:
        key = self._key(record)
        if key is None:
            return
        where = self._by_owner.get(key)
        if where is None:
            return
        where.pop(run_id, None)
        if not where:
            del self._by_owner[key]


#: One shared empty mapping for every owner that has no runs, so "nobody by that name" costs
#: what "nobody with any runs" costs. Never written to: `owned_by` documents that its result is
#: read-only, and a caller that wrote here would corrupt every other empty owner at once.
_NOTHING: dict = {}
