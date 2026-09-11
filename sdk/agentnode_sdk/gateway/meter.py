"""What each run used, written down once, carrying nothing that would be a disclosure.

A managed service has to be able to say who used what. This is that record and nothing more: it is
not billing, it prices nothing, and no money is attached to any number in it. What it exists for is
that when there IS billing, the thing being billed was recorded at the time rather than
reconstructed afterwards from logs that were never meant to answer the question.

## What it carries

The run, the client it belonged to, when it started and ended, how long the sandbox had it, the
ceilings it was granted, how much it wrote, and how it ended.

## What it must never carry, and why that is structural

No token. No pairing code. No artefact. No line of a job's output.

That is not a rule somebody has to remember when adding a field: `record` takes named values and
writes exactly those, there is no field that takes a dictionary somebody could put anything in, and
a test walks a real record looking for every secret a real gateway holds. A meter that grew a
"details" field would be a meter that one day held a token, and the file is one an operator will
reasonably hand to somebody doing accounts.

## What this does not establish

Counting is not charging. And a line here says what a run used, not whether it should have been
allowed to -- that is `allowance.py`, which is consulted before a run starts rather than after.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

#: One line per run, appended. A file rather than the ledger, because the ledger is about replay
#: and is read on every admission -- a record that grows with every run does not belong in it.
METER_NAME = "use-log.jsonl"

#: Exactly the fields a line has. Named here so that adding one is a decision somebody makes on
#: purpose, in a place a reviewer reads, rather than a keyword appearing at a call site.
FIELDS = ("run_id", "client_id", "started_at", "finished_at", "seconds",
          "cpu", "memory_mb", "wall_clock_s", "state", "outcome", "bytes_out",
          "worker_topology", "allowance_sha256")


def record(root: str | os.PathLike[str], *, run_id: str, client_id: str, started_at: float,
           finished_at: float, cpu: float, memory_mb: int, wall_clock_s: int, state: str,
           outcome: str, bytes_out: int, worker_topology: str,
           allowance_sha256: str) -> Path:
    """Write one line about one run.

    Every value is named. There is deliberately no parameter that takes free-form content: a
    meter with somewhere to put "anything else" is a meter that will one day hold a secret.
    """
    line = {
        "run_id": str(run_id),
        "client_id": str(client_id),
        "started_at": float(started_at),
        "finished_at": float(finished_at),
        "seconds": round(max(0.0, float(finished_at) - float(started_at)), 3),
        "cpu": float(cpu),
        "memory_mb": int(memory_mb),
        "wall_clock_s": int(wall_clock_s),
        "state": str(state),
        "outcome": str(outcome),
        # How much the job wrote, not what it wrote.
        "bytes_out": int(bytes_out),
        "worker_topology": str(worker_topology),
        "allowance_sha256": str(allowance_sha256),
    }
    assert set(line) == set(FIELDS), "a line has exactly the fields this module declares"
    path = Path(root) / METER_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    # Opened with its permissions on creation rather than narrowed afterwards, and appended to,
    # so two runs finishing together do not overwrite one another.
    handle = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(handle, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n")
    return path


def read(root: str | os.PathLike[str]) -> list[dict]:
    """Every line, for an operator looking at what was used."""
    path = Path(root) / METER_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:                                    # pragma: no cover - a torn write
            continue
    return out


def summarise(root: str | os.PathLike[str], since: float = 0.0) -> dict[str, dict]:
    """Per client: how many runs and how many seconds. What an operator actually asks."""
    totals: dict[str, dict] = {}
    for line in read(root):
        if float(line.get("started_at", 0)) < since:
            continue
        who = str(line.get("client_id") or "")
        at = totals.setdefault(who, {"runs": 0, "seconds": 0.0, "bytes_out": 0})
        at["runs"] += 1
        at["seconds"] = round(at["seconds"] + float(line.get("seconds", 0.0)), 3)
        at["bytes_out"] += int(line.get("bytes_out", 0))
    return totals


def now() -> float:                                           # pragma: no cover - a seam
    return time.time()
