"""What the process was doing when something took too long.

`test_verification_run.py` has been failing intermittently in CI -- always a submission to the
session gateway timing out after thirty seconds, always late in the run, on a different Python
version each time. Re-running it until it passes turns a reliability question into a coin toss
and answers nothing, so this module exists to make the failure SAY something.

Two things are recorded, because they answer different questions.

A **snapshot** is cheap and is taken around every test when `AGENTNODE_DIAGNOSE` is set: how many
threads are alive, what they are called, and how many file descriptors are open. A fault that
accumulates over a run shows up as a line going up, and a line going up is a cause rather than a
symptom. One such cause has already been found and fixed this way -- two watcher threads per
gateway that outlived every server the suite created -- and the point of keeping this is that the
next one is found the same way instead of being re-run past.

A **dump** is expensive and is taken only when something has already gone wrong: the stack of
every live thread. If a submission times out because the server thread is blocked, that stack
says where. If it times out because nothing is blocked and the machine is simply saturated, the
absence of a blocked stack says that instead. Those are different faults with different fixes,
and telling them apart is the whole job here.

Nothing in this module is imported by the product.
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import traceback
from collections import Counter

#: Set in CI so a run leaves a growth curve behind. Off by default: a snapshot per test is cheap
#: but not free, and a developer running one test does not need it.
DIAGNOSE = "AGENTNODE_DIAGNOSE"

#: Where the per-test snapshots go when it is on. One JSON object per line, so a run that is
#: killed half way still leaves readable evidence.
TRAIL_NAME = "reliability-trail.jsonl"


def enabled() -> bool:
    return bool(os.environ.get(DIAGNOSE))


def open_descriptors() -> int:
    """How many file descriptors this process holds, or -1 where that cannot be asked.

    A leaked socket, a leaked server, a temporary directory never closed: all of them show here
    before they show anywhere else. On platforms without /proc this returns -1 rather than a
    guess, because a made-up number in a diagnostic is worse than no number.
    """
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return -1


def snapshot() -> dict:
    """Cheap. Safe to take around every test."""
    alive = threading.enumerate()
    return {
        "threads": len(alive),
        "descriptors": open_descriptors(),
        "by_name": dict(Counter(_shape_of(t.name) for t in alive)),
    }


def _shape_of(name: str) -> str:
    """Thread names carry counters (`Thread-41 (serve_forever)`), and counting the names as they
    come would produce one bucket per thread and no signal at all. The shape is what matters."""
    inside = name.split("(", 1)[1].rstrip(")") if "(" in name else ""
    if inside:
        return inside
    for known in ("ThreadPoolExecutor", "Thread-", "asyncio"):
        if name.startswith(known):
            return known.rstrip("-")
    return name


def dump_stacks() -> str:
    """Expensive, and only worth taking once something has gone wrong."""
    out = io.StringIO()
    by_id = {t.ident: t for t in threading.enumerate()}
    for ident, frame in sys._current_frames().items():
        thread = by_id.get(ident)
        out.write("\n--- %s (%s)\n" % (getattr(thread, "name", "?"), ident))
        out.write("".join(traceback.format_stack(frame)))
    return out.getvalue()


def what_was_it_doing(what: str, seconds: float) -> str:
    """The whole diagnosis, as a string to attach to the failure that prompted it."""
    counted = snapshot()
    return (
        "\n\n=== %s did not finish within %.1fs ===\n"
        "threads: %d   descriptors: %d\n"
        "by name: %s\n"
        "%s"
        % (what, seconds, counted["threads"], counted["descriptors"],
           json.dumps(counted["by_name"], sort_keys=True), dump_stacks())
    )


def record(trail: str, test: str, when: str) -> None:
    """Append one snapshot to the trail. Never raises: a diagnostic that breaks a run is worse
    than no diagnostic."""
    try:
        line = dict(snapshot(), test=test, when=when)
        with open(trail, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, sort_keys=True) + "\n")
    except Exception:                                         # noqa: BLE001
        pass
