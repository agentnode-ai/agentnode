"""One of the two processes in the recovery contest of `test_state_consistency.py`.

## Why this file exists

The contest is: two gateways start on one directory holding a run that was mid-flight and has no
signed line, and both try to close it. The invariants are the product's -- exactly one signed line,
one quota figure equal to it, both processes agreeing afterwards, an established cleanup not
weakened. None of those mean anything unless the two recoveries really met.

How "they really met" used to be established: each child recorded the wall-clock window of its own
contended region and the test asserted that the windows overlapped. That is a statement about the
scheduler. On a shared runner the second child began 6ms after the first had finished, the test
correctly refused to pass on a sequence, and `sdk (3.10)` went red -- for a reason that had nothing
to do with the product.

So the precondition is established here instead, at the place the product actually serialises these
two recoveries:

    agentnode_sdk/gateway/meter.py :: _writing(root) -> ProcessLock(<root>/use-log.jsonl)

That is the lock inside which `meter.record` reads the tail, refuses a second line for a run that
already has one (`AlreadyRecorded`), and appends. `server.py` takes no recovery-wide lock -- it does
not import `ProcessLock` at all -- so this one lock is the whole of the cross-process exclusion the
contended operation has.

## What makes it deterministic rather than lucky

`ProcessLock` is a retry loop around a NON-BLOCKING `_try_acquire`, which is `fcntl.flock(LOCK_NB)`
on Linux and `msvcrt.locking(LK_NBLCK)` on Windows. So a REFUSED acquisition is a positive event
that happened, not the absence of one. That is the hinge of this design:

  * the first child into the lock becomes the HOLDER -- which one that is, is decided by the lock
    itself -- and parks inside it until the parent explicitly releases it;
  * the other child reaches the acquisition and is refused it, and says so;
  * the parent releases the holder only once both of those are on record;
  * everything is written to one journal whose every line is appended under a lock of its own, so
    the file is a TOTAL ORDER of events and the parent reads the order rather than any timestamp.

No sleep, no elapsed time and no "near enough at the same time" is evidence of anything here. The
product's own 20ms poll inside `ProcessLock` is not evidence either; the refusal it produces is.
Deadlines exist only to turn a hang into a sentence instead of a stuck test.

## What is instrumented, and what is not

Nothing of the product's behaviour is replaced. `_try_acquire` is the product's own and its result is
passed through untouched; `meter.record` is the product's own and `AlreadyRecorded` is its own
answer. Two things are added around them -- the events -- and ONE thing is changed, which is said
rather than hidden: the lock's patience. The product refuses after 10s rather than proceeding on
state it cannot trust, which is right in production and wrong in a test where the holder is parked
on purpose. A 10s cliff would put the parent's scheduling back into the verdict, which is the whole
thing this file removes. So the patience is raised, and what a deadline can still catch is a hang.

This is a program, not a test module: it is named so that pytest does not collect it, its entry point
is a module-level function, and it is run in a fresh interpreter by `subprocess`. Nothing is captured
from a parent's memory, which is what makes it safe under spawn semantics as well as fork.
"""
from __future__ import annotations

import json
import os
import sys
import time

#: Every event this child can record. The parent asserts on these names, so they are defined here and
#: imported there rather than spelled twice.
HOLDS = "holds-the-production-lock"
PARKED = "parked-inside-the-lock"
RELEASED = "released-by-the-parent"
REFUSED = "refused-while-another-process-held-it"
RELEASES = "releases-the-production-lock"
WROTE = "wrote-the-one-signed-line"
REFUSED_A_SECOND_LINE = "was-refused-a-second-line"
ENTERED = "entered-the-target-runs-own-close"

#: How long any wait here may last before it is called a HANG. It is not a measurement: nothing is
#: concluded from how long a wait took, only from whether it ended by itself.
HANG = 240.0


def announce(journal: str, label: str, event: str, **extra) -> None:
    """Append one event to the journal under a lock of its own.

    The journal is the instrument, and what it provides is ORDER: every append holds this lock, so
    the order of the lines is a real happened-before between the two processes. The parent reads that
    order. It never subtracts two numbers.
    """
    from agentnode_sdk.gateway.filelock import ProcessLock

    row = {"label": label, "event": event, "pid": os.getpid()}
    row.update(extra)
    line = json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
    with ProcessLock(journal):
        with open(journal, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())


def wait_until_it_exists(path: str, what: str) -> None:
    """Wait for a file to appear, and call the deadline a hang. The only thing a deadline does here."""
    until = time.monotonic() + HANG
    while not os.path.exists(path):
        if time.monotonic() >= until:
            raise AssertionError(
                "%s never arrived within %.0fs, so this child gave up waiting. That is a hang and it "
                "is reported as one: nothing about contention is concluded from it." % (what, HANG))
        time.sleep(0.002)


class TheLockWithAWitness:
    """The real `ProcessLock`, with every refusal and every acquisition written down.

    The first process to acquire it becomes the holder and parks until the parent releases it. The
    other one is refused, and its refusal is the evidence that it reached the acquisition and could
    not pass it while the holder held it.
    """

    def __init__(self, lock, journal: str, label: str, claim: str, go: str) -> None:
        self.lock = lock
        self.journal = journal
        self.label = label
        self.claim = claim
        self.go = go
        self.refusals: list = []
        self.holder = False

    def _claim_it(self) -> bool:
        """Whoever is inside the lock first creates this file, so the LOCK decides who parks."""
        try:
            handle = os.open(self.claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False
        os.close(handle)
        return True

    def __enter__(self) -> "TheLockWithAWitness":
        self.lock.timeout = max(float(self.lock.timeout), HANG)
        inner = self.lock._try_acquire

        def try_acquire(fd):
            got = inner(fd)
            if not got and not self.refusals:
                # ONE line, not one per poll: the first refusal is the fact, the rest are the
                # product's retry loop and would only bury the record.
                self.refusals.append(1)
                announce(self.journal, self.label, REFUSED, lock=self.lock.path.name)
            return got

        self.lock._try_acquire = try_acquire
        self.lock.__enter__()
        self.holder = self._claim_it()
        announce(self.journal, self.label, HOLDS, holder=self.holder,
                 refused_first=bool(self.refusals), lock=self.lock.path.name)
        if self.holder:
            announce(self.journal, self.label, PARKED)
            wait_until_it_exists(self.go, "the parent's release of the parked holder")
            announce(self.journal, self.label, RELEASED)
        return self

    def __exit__(self, *exc):
        # Announced BEFORE the lock is actually let go, so the other process cannot possibly hold it
        # earlier than this line: `holder releases` < `waiter holds` is then a statement the lock
        # itself enforces, and a broken lock shows up as the waiter holding before this line.
        announce(self.journal, self.label, RELEASES, holder=self.holder)
        return self.lock.__exit__(*exc)


def watch_the_production_lock(root: str, journal: str, label: str, claim: str, go: str) -> None:
    """Hand out the same lock, wrapped. Only the meter's lock on this root is wrapped, nothing else."""
    from agentnode_sdk.gateway import meter

    target = os.path.abspath(os.path.join(str(root), meter.METER_NAME)) + ".lock"
    inner = meter._writing

    def writing(the_root):
        lock = inner(the_root)
        if os.path.abspath(str(lock.path)) != target:
            return lock
        return TheLockWithAWitness(lock, journal, label, claim, go)

    meter._writing = writing


def watch_the_one_line_rule(root: str, run_id: str, journal: str, label: str) -> None:
    """Record who wrote the one line and who was refused a second one.

    This is the product's own answer rather than the harness's: `AlreadyRecorded` is raised by
    `meter.record` against the file, inside the lock. A process that sees it has seen the other
    process's line, which is what "it could not write its own while the other held the lock" means in
    the product's terms rather than in the test's.
    """
    from agentnode_sdk.gateway import meter

    inner = meter.record

    def record(the_root, **kw):
        if str(kw.get("run_id") or "") != str(run_id):
            return inner(the_root, **kw)
        try:
            written = inner(the_root, **kw)
        except meter.AlreadyRecorded as refusal:
            announce(journal, label, REFUSED_A_SECOND_LINE, said=str(refusal)[:200])
            raise
        # This lands in the journal AFTER the holder's `releases` line, and that is not a mistake:
        # the wrapper only returns once `meter.record` has left the `with _writing(root)` block, so
        # the announcement of a write necessarily follows the announcement of the release. The write
        # itself happened inside the lock; what is ordered here is the saying of it.
        announce(journal, label, WROTE)
        return written

    meter.record = record


def watch_the_target_runs_close(run_id: str, journal: str, label: str) -> list:
    """Say when this child entered the close of THIS run, so neither child can have sat it out."""
    from agentnode_sdk.gateway.server import GatewayService

    entered: list = []
    inner = GatewayService._close_an_interrupted_run

    def close(service, record, entry, *a, **kw):
        rid = str(getattr(record, "run_id", "") or (entry or {}).get("run_id") or "")
        if rid != str(run_id):
            return inner(service, record, entry, *a, **kw)
        entered.append(rid)
        announce(journal, label, ENTERED)
        return inner(service, record, entry, *a, **kw)

    GatewayService._close_an_interrupted_run = close
    return entered


def main(argv: list) -> int:
    root, run_id, label, journal, claim, go, barrier = argv[1:8]

    from agentnode_sdk.gateway.identity import GatewayState
    from agentnode_sdk.gateway.server import GatewayService
    from tests.test_em3c_gateway import StandInBackend

    entered = watch_the_target_runs_close(run_id, journal, label)
    watch_the_production_lock(root, journal, label, claim, go)
    watch_the_one_line_rule(root, run_id, journal, label)

    state = GatewayState(root, version="test")

    # THE HANDSHAKE COMES FIRST, because the contended work IS the constructor: building a
    # GatewayService runs crash recovery. Announcing readiness afterwards would leave the recovery
    # outside the barrier and let process startup serialise the very thing this is about --
    # STATE-CONSISTENCY-0004's F-RECOVERY-HANDSHAKE-AFTER-RECOVERY, and it still applies here.
    open(barrier + ".ready." + str(os.getpid()), "w").close()
    wait_until_it_exists(barrier, "the parent's barrier")

    service = GatewayService(state, backend=StandInBackend(), recover=True)

    said = {"pid": os.getpid(), "label": label, "mode": "recover",
            "entered_the_close": len(entered)}

    def figures():
        return service.use.what_a_run_was_charged(["dev-1"], run_id)

    was = service.ledger.run_entry(run_id) or {}
    said["settled_before"] = was.get("settled_as") or ""
    said["cleanup_before"] = was.get("cleanup")
    said["quota_before"] = figures().get("dev-1")

    # THE PRODUCT'S OWN PATH for the quota, not an invented figure: it reads the signed log and sets
    # what the line says. Both children run it, so whatever the interleaving the file must end with
    # one figure equal to that line.
    said["reconciled"] = service.reconcile_every_record_against_the_signed_log()
    after = service.ledger.run_entry(run_id) or {}
    said["settled_after"] = after.get("settled_as") or ""
    said["cleanup_after"] = after.get("cleanup")
    said["quota_after"] = figures().get("dev-1")

    print("RESULT " + json.dumps(said))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
