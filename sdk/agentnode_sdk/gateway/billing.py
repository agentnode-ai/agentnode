"""What would be charged for, if anybody had decided what to charge.

**Nothing here prices anything and no money appears in this file.** Choosing a payment provider
and setting a price are decisions for whoever runs this service, and a module that quietly
contained a number would be making one of them by accident. What this does is turn a verified
record of use into billable events, so that when a price exists it is applied to something that
was recorded at the time rather than reconstructed afterwards from logs that were never meant to
answer the question.

## Why a statement refuses to be produced from a record that does not verify

An invoice is an assertion about somebody else's money. The metering chain exists so that a line
cannot be edited, removed, reordered or truncated without the change being visible -- and if that
check fails, the honest response is not to bill the total anyway with a footnote. It is to refuse,
loudly, and let a person look. A billing system that produced a number from a record it knew was
damaged would be worse than one with no checking at all, because the number would look checked.

So `statement` verifies first and raises `CannotBeBilled` if the record does not hold. There is no
flag to skip it.

## What a billable event is bound to

Every event names the account, the run, the operator policy (by digest and by version), the worker
and the ceilings the run was admitted under. That is not decoration: a customer disputing a charge
asks *what were the rules when this ran*, and an answer that requires reading a configuration file
which has since been edited is not an answer.

## What this deliberately does not do

* No provider. No API call, no webhook, no client library, nothing to configure.
* No price, no currency, no tax, no rounding rule, no plan, no discount.
* No decision about what a unit IS -- seconds, runs and bytes are all reported, because which of
  them is billable is the same decision as the price.

The interface is the part that can be built without those. Everything else waits.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


class CannotBeBilled(Exception):
    """The record of use does not verify, so nothing is derived from it."""


@dataclass(frozen=True)
class Billable:
    """One run, as something that could be charged for. No amount, by design."""

    run_id: str
    account_id: str
    #: The three quantities recorded. Which of them is the unit is a pricing decision.
    seconds: float
    runs: int
    bytes_out: int
    #: Under which rules.
    operator_policy_sha256: str
    operator_policy_version: int
    #: Where.
    worker_id: str
    worker_topology: str
    #: What ceilings were in force, by digest, so a record binds the configuration it was
    #: admitted under rather than the one in place when somebody asks.
    allowance_sha256: str
    started_at: float
    finished_at: float

    def as_dict(self) -> dict:
        from dataclasses import asdict

        return asdict(self)


def events(root: str | os.PathLike[str], since: float = 0.0,
           until: float = 0.0) -> list[Billable]:
    """Every run in the window, as a billable event. Verifies the record first."""
    from agentnode_sdk.gateway import meter

    held = meter.verify(root)
    if not held.get("ok"):
        raise CannotBeBilled(
            "the record of what was used does not verify (%s). Nothing is derived from it: a "
            "total produced from a record known to be damaged would look checked and would not "
            "be." % held.get("detail", "no detail"))

    out = []
    for line in meter.read(root):
        if meter.is_a_tombstone(line):
            # Erased at somebody's request. It is not billable and it is not silently counted:
            # `statement` reports how many there were, because a customer whose lines were
            # erased has a right to know the total is not the whole of what happened.
            continue
        if "seq" not in line:
            # Written before this gateway kept a chain, so nothing vouches for it. Counted as
            # unbillable rather than trusted.
            continue
        finished = float(line.get("finished_at") or 0.0)
        if finished < since or (until and finished > until):
            continue
        out.append(Billable(
            run_id=str(line.get("run_id") or ""),
            account_id=str(line.get("account_id") or ""),
            seconds=float(line.get("seconds") or 0.0),
            runs=1,
            bytes_out=int(line.get("bytes_out") or 0),
            operator_policy_sha256=str(line.get("operator_policy_sha256") or ""),
            operator_policy_version=int(line.get("operator_policy_version") or -1),
            worker_id=str(line.get("worker_id") or ""),
            worker_topology=str(line.get("worker_topology") or ""),
            allowance_sha256=str(line.get("allowance_sha256") or ""),
            started_at=float(line.get("started_at") or 0.0),
            finished_at=finished,
        ))
    return out


def statement(root: str | os.PathLike[str], since: float = 0.0,
              until: float = 0.0) -> dict:
    """What each account used in the window, with everything an invoice would have to cite.

    Deliberately reports what it CANNOT bill as well as what it can: runs with no account,
    lines that predate the chain, and lines that were erased. A statement that quietly omitted
    those would read as complete.
    """
    from agentnode_sdk.gateway import meter

    by_account: dict[str, dict] = {}
    unattributable = 0
    for event in events(root, since, until):
        if not event.account_id:
            unattributable += 1
            continue
        totals = by_account.setdefault(event.account_id, {
            "runs": 0, "seconds": 0.0, "bytes_out": 0,
            "operator_policy_versions": set(), "workers": set(),
        })
        totals["runs"] += 1
        totals["seconds"] = round(totals["seconds"] + event.seconds, 3)
        totals["bytes_out"] += event.bytes_out
        if event.operator_policy_version > 0:
            totals["operator_policy_versions"].add(event.operator_policy_version)
        if event.worker_id:
            totals["workers"].add(event.worker_id)
    for totals in by_account.values():
        totals["operator_policy_versions"] = sorted(totals["operator_policy_versions"])
        totals["workers"] = sorted(totals["workers"])

    lines = meter.read(root)
    return {
        "accounts": by_account,
        "runs_that_could_not_be_attributed": unattributable,
        "lines_erased_on_request": sum(1 for line in lines if meter.is_a_tombstone(line)),
        "lines_that_predate_the_chain": sum(1 for line in lines if "seq" not in line),
        "what_this_is_not": [
            "a price. Nothing here is priced, and no currency appears anywhere in it.",
            "an invoice. There is no provider, no plan and no amount.",
            "a claim that the gateway is honest: it holds the key it signs this with, so this "
            "establishes that nobody WITHOUT that key altered the record.",
        ],
    }
