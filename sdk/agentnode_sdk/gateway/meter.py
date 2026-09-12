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

## Why it is a chain, and what that is worth

Every line carries the digest of the line before it and a signature over both. Removing a line,
reordering two, or editing a number in one breaks the chain from that point on, and `verify` says
where. Appending a line that verifies needs the gateway's meter key.

That is tamper-EVIDENT, which is a smaller and more honest claim than tamper-proof, and the
difference matters:

* what it does establish -- nobody who lacks the key can change this file without the change
  being visible: not another account on the machine, not a restored backup, not a truncating
  editor, not a copy that lost its tail;
* what it does NOT establish -- that the gateway itself is honest. The process that writes this
  holds the key, so a gateway that wanted to lie could write a chain that verifies perfectly. A
  log cannot be evidence against the thing that writes it. Making that impossible needs a
  counter-signature from somewhere else, and there is nowhere else yet.

The signature is Ed25519, and the public half is written beside the log. Anyone can check the
whole file with that alone -- no secret, and nothing to ask the gateway for.

## What this does not establish

Counting is not charging. And a line here says what a run used, not whether it should have been
allowed to -- that is `allowance.py`, which is consulted before a run starts rather than after.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from agentnode_sdk.worker import what_it_does_not_establish

#: One line per run, appended. A file rather than the ledger, because the ledger is about replay
#: and is read on every admission -- a record that grows with every run does not belong in it.
METER_NAME = "use-log.jsonl"

#: Exactly the fields a line has. Named here so that adding one is a decision somebody makes on
#: purpose, in a place a reviewer reads, rather than a keyword appearing at a call site.
FIELDS = ("run_id", "client_id", "started_at", "finished_at", "seconds",
          "cpu", "memory_mb", "wall_clock_s", "state", "outcome", "bytes_out",
          "worker_topology", "worker_topology_means", "allowance_sha256",
          "allowance_admitted_under")

#: What binds one line to the one before it. Not in FIELDS: those are what a line SAYS, these are
#: what makes it hard to change, and keeping them apart stops a reader mistaking one for the
#: other.
SEALED = ("seq", "prev", "signature")

#: The key this gateway signs its meter with, and the public half beside it. The private half is
#: the gateway's; the public half exists so that checking the log needs nothing from the gateway.
METER_KEY_NAME = "meter-key.pem"
METER_PUBLIC_NAME = "meter-key.pub"

#: Where the chain currently ends, signed, kept OUTSIDE the log.
#:
#: A hash chain catches a line that was edited, removed from the middle, or reordered -- but
#: every PREFIX of a chain is itself a perfectly good chain. Cutting the tail off, which is the
#: obvious way to hide what happened recently, leaves a file that verifies. Nothing inside a file
#: can establish how long that file is supposed to be, so this is beside it: how many lines there
#: are and what the last one digests to, signed. Truncating the log then disagrees with a
#: statement the truncator cannot rewrite.
HEAD_NAME = "use-log.head"

#: The digest the first line points back at. A chain has to start somewhere, and starting at a
#: named constant rather than at "" means a file whose first line was removed does not look like
#: a file that always began there.
GENESIS = "the first line of this gateway's meter"


def _canonical(line: dict) -> bytes:
    """The bytes that are digested and signed. One spelling, so two readers cannot disagree."""
    return json.dumps(line, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest_of(line: dict) -> str:
    return hashlib.sha256(_canonical(line)).hexdigest()


def signing_key(root: str | os.PathLike[str]):
    """This gateway's meter key, made once and then read. The public half is written beside it."""
    from agentnode_sdk.signing_key import (
        generate_ed25519_keypair,
        load_signing_key,
        save_signing_key,
    )

    path = Path(root) / METER_KEY_NAME
    if path.exists():
        return load_signing_key(path)
    private, public = generate_ed25519_keypair()
    save_signing_key(private, path)
    (Path(root) / METER_PUBLIC_NAME).write_text(public.hex() + "\n", encoding="utf-8")
    return private


def public_key(root: str | os.PathLike[str]) -> bytes:
    """The half anyone may have. Checking the log needs this and nothing else."""
    try:
        return bytes.fromhex((Path(root) / METER_PUBLIC_NAME).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return b""


def record(root: str | os.PathLike[str], *, run_id: str, client_id: str, started_at: float,
           finished_at: float, cpu: float, memory_mb: int, wall_clock_s: int, state: str,
           outcome: str, bytes_out: int, worker_topology: str,
           allowance_sha256: str,
           allowance_admitted_under: dict | None = None) -> Path:
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
        # The label and what it means, together. A reader who meets "single-host-development" in
        # a record months from now has no other way to know what it does not protect against,
        # and that is the reason the label is there at all.
        "worker_topology_means": what_it_does_not_establish(worker_topology),
        "allowance_sha256": str(allowance_sha256),
        # The digest says WHICH ceilings, and a digest cannot be turned back into numbers. A
        # reader holding one line has to be able to see what was actually in force when the run
        # was admitted, months later, without a copy of a configuration file that has since been
        # edited -- otherwise the binding proves only that something was bound.
        "allowance_admitted_under": {k: v for k, v in sorted(
            dict(allowance_admitted_under or {}).items())},
    }
    assert set(line) == set(FIELDS), "a line has exactly the fields this module declares"
    path = Path(root) / METER_NAME
    path.parent.mkdir(parents=True, exist_ok=True)

    from agentnode_sdk.signing_key import sign_payload

    # Read the tail to find where the chain is, and write under the same lock, because two runs
    # finishing together must not both claim one sequence number. Two lines with the same `seq`
    # and the same `prev` is a fork, and a fork is what the chain exists to make visible.
    with _writing(root):
        # The last CHAINED line, not the last line. A log that predates the chain ends in lines
        # with no `seq`, and pointing the first chained line at one of those would make the chain
        # start somewhere `verify` cannot begin -- it starts at GENESIS or it does not start.
        so_far = [r for r in read(root) if "seq" in r]
        previous = so_far[-1] if so_far else None
        line["seq"] = (int(previous.get("seq", 0)) + 1) if previous else 1
        line["prev"] = _digest_of(_without_signature(previous)) if previous else GENESIS
        line["signature"] = sign_payload(_canonical(line), signing_key(root)).hex()

        # Opened with its permissions on creation rather than narrowed afterwards, and appended
        # to, so two runs finishing together do not overwrite one another.
        handle = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(handle, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n")
        _write_head(root, line)
    return path


def _write_head(root, last: dict) -> None:
    """Say where the chain ends, signed, so cutting the tail off disagrees with something."""
    from agentnode_sdk.signing_key import sign_payload

    head = {"seq": int(last["seq"]), "digest": _digest_of(_without_signature(last))}
    head["signature"] = sign_payload(_canonical(head), signing_key(root)).hex()
    path = Path(root) / HEAD_NAME
    tmp = path.with_suffix(".new")
    tmp.write_text(json.dumps(head, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _read_head(root) -> dict | None:
    try:
        return json.loads((Path(root) / HEAD_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _without_signature(line: dict) -> dict:
    """A line as it was when it was signed: everything except the signature over it."""
    return {k: v for k, v in line.items() if k != "signature"}


def _writing(root):
    """One writer at a time, across processes. The gateway's own lock, not a new idea."""
    from agentnode_sdk.gateway.filelock import ProcessLock

    return ProcessLock(Path(root) / METER_NAME)


def verify(root: str | os.PathLike[str]) -> dict:
    """Walk the chain and say whether it holds, and where it stops holding if it does not.

    Reports the FIRST break and stops. A reader told "line 40 and line 700 are wrong" has to work
    out whether the second is a consequence of the first; a reader told where it first stops
    agreeing knows exactly how much of the file is still worth reading -- everything before it.
    """
    from agentnode_sdk.signature import verify_signature

    lines = read(root)
    public = public_key(root)
    if not lines:
        return {"ok": True, "lines": 0, "detail": "nothing has been recorded here yet"}
    if not public:
        return {"ok": False, "lines": len(lines), "at": 0,
                "detail": "there is no public key beside this log, so nothing in it can be "
                          "checked, and a log that cannot be checked is not evidence"}
    # Lines written before this gateway kept a chain carry no `seq`. They cannot be signed now --
    # signing them today would be this gateway vouching for what it did not record at the time,
    # which is forging, not migrating. So they are counted and named as unchecked, and they are
    # allowed only at the FRONT: an unchained line appearing later means one was replaced.
    before_the_chain = 0
    for line in lines:
        if "seq" in line:
            break
        before_the_chain += 1
    chained = lines[before_the_chain:]
    if not chained:
        return {"ok": False, "lines": len(lines), "at": 0, "unchecked": before_the_chain,
                "detail": "all %d line(s) here were written before this gateway kept a chain, so "
                          "none of them can be checked" % len(lines)}

    expected_prev, expected_seq = GENESIS, 1
    for index, line in enumerate(chained, start=before_the_chain + 1):
        where = {"ok": False, "lines": len(lines), "at": index,
                 "run_id": str(line.get("run_id", ""))[:32]}
        if int(line.get("seq", 0)) != expected_seq:
            return {**where, "detail": "line %d says it is number %s, and the one before it was "
                                       "number %d -- a line has been removed or reordered"
                                       % (index, line.get("seq"), expected_seq - 1)}
        if str(line.get("prev", "")) != expected_prev:
            return {**where, "detail": "line %d does not point back at the line before it, so "
                                       "something between them was changed or taken out" % index}
        try:
            raw = bytes.fromhex(str(line.get("signature", "")))
        except ValueError:
            raw = b""
        if not raw or not verify_signature(_canonical(_without_signature(line)), raw, public):
            return {**where, "detail": "line %d is not signed by the key this gateway signs "
                                       "with, so it was written by something else or changed "
                                       "after it was written" % index}
        expected_prev = _digest_of(_without_signature(line))
        expected_seq += 1
    # The chain holds. Whether it is the WHOLE chain is a different question, and one no file
    # can answer about itself.
    from agentnode_sdk.signature import verify_signature as _check

    head = _read_head(root)
    if head is None:
        return {"ok": False, "lines": len(lines), "at": len(lines),
                "detail": "every line checks out, but there is nothing here saying where this "
                          "log is supposed to end, so lines taken off the end would not show"}
    try:
        head_raw = bytes.fromhex(str(head.get("signature", "")))
    except ValueError:
        head_raw = b""
    if not head_raw or not _check(_canonical({k: v for k, v in head.items()
                                              if k != "signature"}), head_raw, public):
        return {"ok": False, "lines": len(lines), "at": len(lines),
                "detail": "the note saying where this log ends is not signed by this gateway, so "
                          "it cannot be used to tell whether anything was taken off the end"}
    if int(head.get("seq", 0)) != expected_seq - 1 or str(head.get("digest", "")) != expected_prev:
        return {"ok": False, "lines": len(lines), "at": len(lines),
                "detail": "this log ends at line %d, and it is supposed to end at line %s -- "
                          "%s line(s) have been taken off the end"
                          % (expected_seq - 1, head.get("seq"),
                             int(head.get("seq", 0)) - (expected_seq - 1))}
    if before_the_chain:
        return {"ok": True, "lines": len(lines), "unchecked": before_the_chain,
                "detail": "the %d line(s) after the first %d are signed, point at the one before "
                          "each, and end where they are supposed to. The first %d were written "
                          "before this gateway kept a chain and cannot be checked at all"
                          % (len(chained), before_the_chain, before_the_chain)}
    return {"ok": True, "lines": len(lines), "unchecked": 0,
            "detail": "every line is signed, points at the one before it, and the log ends "
                      "where it is supposed to"}


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
