"""What a gateway is made of, so a restore can be checked against what was taken.

`deploy/backup-and-restore.sh` used to ask whether the restored state *looked* all right: does
the metering chain verify, does it know its own identity, are there some devices, is the policy
key here, are the permissions 700. Every one of those passes on a restore that silently came back
with half the accounts, no sessions, an empty audit and no tombstones. "It checks out" was being
asked of the copy alone, and a copy has nothing to disagree with.

So a backup writes down WHAT IT CONTAINED, class by class, and a restore recomputes the same
thing and compares. A class that came back different is named. A class that came back missing is
named. Both are failures rather than notes.

## Where the list comes from

Not from here. The aged classes come from `retention.CLASSES`, which is the table the sweep
already walks -- so a class added there is in the backup drill the same day, and cannot be
forgotten in one place while being handled in the other. What this module adds is `BESIDES`: the
files a gateway keeps that have no age, which the retention table deliberately does not cover.

Between them they are everything in a gateway's directory, and `test_backup_drill.py` reads that
claim against a real one: a file that is in neither is a file this drill would not notice losing.

## What is written down, and what deliberately is not

For records: how many there are, and a digest over what IDENTIFIES them -- account ids, device
ids, session names, run ids. Enough that losing one, gaining one or replacing one shows up.

For keys and certificates: that the file is there, and its length. Never a digest of its content.
A manifest travels with the archive and sometimes out of it; a digest of a private key in a file
somebody is less careful with is an oracle for that key, and "we only stored a hash" is exactly
the sentence that precedes that being a problem.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from agentnode_sdk.gateway import retention

#: The file a backup writes its manifest to, inside the backup directory rather than inside the
#: state. It describes the archive; it is not part of what the archive contains.
MANIFEST_NAME = "WHAT_IS_IN_IT.json"

#: The directory beside the state one, holding the key that authenticates the operator policy.
#: Named here because a backup that takes the state alone restores to a gateway that refuses
#: every job -- correctly, since it cannot tell whether its own policy was changed by something
#: else. A drill that never started the restored gateway did not notice for a long time.
SECRET_SUFFIX = ".secret"

# How to summarise each shape of file.
LINES = "lines"        # JSON-lines: count them, and digest an identifying field per line
KEYS = "keys"          # one JSON object keyed by id: count and digest the keys
NESTED = "nested"      # one JSON object of lists keyed by id: count keys and entries
PRESENT = "present"    # opaque or secret: it is here, and it is this long. Never its content
WHOLE = "whole"        # small and not secret: digest the whole thing

#: Which identifying field to digest, per JSON-lines file. Not the whole line: a line carries a
#: timestamp, and a manifest that changed whenever a clock moved would be compared by nobody.
WHAT_IDENTIFIES = {
    "audit.jsonl": ("at", "operation", "account", "via", "outcome"),
    "use-log.jsonl": ("run_id",),
    "events.jsonl": ("at", "what"),
    "exports.jsonl": ("account_id", "at"),
}

#: Everything a gateway keeps that has no age, and therefore is not in `retention.CLASSES`.
#: The reason is part of the entry: a file listed here without one is a file somebody added to
#: stop a test failing.
BESIDES = {
    "identity.json": (WHOLE, "which gateway this is; it has no age"),
    "tokens.json": (KEYS, "credentials; removed by withdrawal and deletion, not by age"),
    "accounts.json": (KEYS, "the customers themselves; removed by deletion, not by age"),
    "pairing.json": (WHOLE, "the live invitation; single-use and short-lived by construction"),
    "pairing-throttle.json": (WHOLE, "failed pairing attempts, with their own lockout window"),
    "pairing-admission.json": (WHOLE, "the pairing attempt budget, with its own window"),
    "allowance.json": (WHOLE, "the operator's ceilings; configuration, not a record"),
    "retention.json": (WHOLE, "the retention periods themselves"),
    # Configuration, not a record: WHERE this gateway writes its sealed archives. It has
    # no age of its own, and the period that governs what it points at is a retention
    # class. Undeclared, it made the drill refuse on a real machine with "something else
    # is unaccounted for" -- which is the manifest check doing exactly its job, on a file
    # this product had started writing without saying so.
    "backups.json": (WHOLE, "where the sealed archives are kept; configuration"),
    # A fact about THIS INSTALLATION -- which interpreter, which artefact, which commit.
    # It is in the manifest because the drill must know every file that is here, and it is
    # excluded from a RESTORE because a backup taken on one machine must not hand the
    # receiving machine the sending machine's idea of what it is allowed to run as. The
    # module that writes it says "beside the state, not inside it"; it is inside it, so
    # that the gateway can read it as its own user, and the exclusion is what makes that
    # safe rather than a contradiction left lying about.
    "runtime-pin.json": (WHOLE, "what this installation is allowed to run as; NOT restored"),
    "retention-last-swept.json": (WHOLE, "when the last sweep ran"),
    "operator-policy-versions.json": (WHOLE,
                                      "the ordering of this gateway's own policies"),
    "conformance.json": (WHOLE, "the measurement; replaced, and invalidated by change"),
    "meter-key.pem": (PRESENT, "the signing key"),
    "meter-key.pub": (PRESENT, "its public half"),
    "use-log.head": (WHOLE, "where the metering chain ends"),
    "stopping.json": (WHOLE, "cancellations in flight"),
    "config.json": (WHOLE, "the gateway's own configuration"),
    "tls-cert.pem": (PRESENT, "its certificate"),
    "tls-key.pem": (PRESENT, "its private key"),
    "active-state.json": (WHOLE,
                          "the operator policy in force, and its authentication tag"),
}

#: The shape of each retention class's file. Separate from `retention.CLASSES` because how a
#: class is SUMMARISED is this module's business and not the sweep's.
FILES = "files"

HOW_THE_AGED_ONES_LOOK = {
    # Files rather than lines or keys -- the only class shaped that way, and the reason
    # it is excluded from the manifest below rather than summarised into it.
    "backups": FILES,
    "audit": LINES,
    "metering": LINES,
    "sessions": KEYS,
    "enrolments": KEYS,
    "ledger": WHOLE,
    "counters": NESTED,
    "rate": NESTED,
    "events": LINES,
    "invitations": KEYS,
    "exports": LINES,
}


#: The one retention class that is NOT inside a backup, named here rather than skipped quietly.
#: `backups` is a period over the sealed archives themselves -- how long a copy is kept before it
#: is removed. Putting it in this table would ask a backup to contain the backups, which is
#: circular, and a `KeyError` here is how that was found rather than by reasoning about it. Every
#: other class in the table IS in a backup, and the test that compares the two still holds.
NOT_IN_A_BACKUP = {"backups"}

#: IN a backup, and deliberately NOT restored. A third category, and it needs to be one: the two
#: that existed said "is here and comes back" and "is not here at all", and the runtime pin is
#: neither. It is here, so the drill has to know about it or it reads as an unaccounted file. It
#: must not come back, because a backup carries the SENDING machine's idea of which interpreter
#: and artefact it may run as, and handing that to the receiving machine tells it it is something
#: it is not -- after which its own start refuses, correctly, for a reason nobody chose.
#:
#: Naming the category is the point. A file quietly skipped by a restore and quietly expected by
#: a drill is how a store goes missing without anybody noticing.
NOT_RESTORED = {"runtime-pin.json"}


def everything_a_gateway_keeps() -> dict:
    """Every file, with how to summarise it and why it is kept. One place, two sources."""
    known = {}
    for name, what in retention.CLASSES.items():
        if name in NOT_IN_A_BACKUP:
            continue
        known[what["file"]] = (HOW_THE_AGED_ONES_LOOK[name], what["is"])
    known.update(BESIDES)
    return known


def _digest(parts) -> str:
    running = hashlib.sha256()
    for part in parts:
        running.update(str(part).encode("utf-8"))
        running.update(b"\x1f")
    return running.hexdigest()[:32]


def _summarise(path: Path, how: str) -> dict:
    """What this file contains, in the terms that make a difference visible."""
    if how == PRESENT:
        return {"present": True, "bytes": path.stat().st_size}
    if how == WHOLE:
        return {"present": True, "digest": _digest([path.read_bytes()])}
    if how == LINES:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        fields = WHAT_IDENTIFIES.get(path.name, ())
        marks = []
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                # A line that does not parse is still a line, and losing it is still a loss.
                marks.append(_digest([line]))
                continue
            marks.append(_digest([entry.get(f, "") for f in fields] if fields else [line]))
        return {"present": True, "count": len(lines), "digest": _digest(sorted(marks))}
    body = json.loads(path.read_text(encoding="utf-8") or "{}")
    if not isinstance(body, dict):
        return {"present": True, "digest": _digest([json.dumps(body, sort_keys=True)])}
    if how == NESTED:
        return {"present": True, "count": len(body),
                "entries": sum(len(v) if isinstance(v, list) else 1 for v in body.values()),
                "digest": _digest(sorted(body))}
    return {"present": True, "count": len(body), "digest": _digest(sorted(body))}


def what_is_in_it(state_dir, secret_dir=None) -> dict:
    """The manifest: one entry per file a gateway keeps, plus what is beside it.

    An absent file is recorded as absent rather than left out, because "this gateway has no
    sessions file" and "this manifest does not mention sessions" are different statements and
    only the first one can be compared to anything.
    """
    root = Path(state_dir)
    secret = Path(secret_dir) if secret_dir is not None else Path(str(root) + SECRET_SUFFIX)

    kept = {}
    for name, (how, _why) in sorted(everything_a_gateway_keeps().items()):
        path = root / name
        if not path.is_file():
            kept[name] = {"present": False}
            continue
        try:
            kept[name] = _summarise(path, how)
        except (OSError, ValueError) as unreadable:
            # UNREADABLE IS NOT EMPTY. A file this cannot read is recorded as unreadable, so the
            # comparison reports it rather than quietly matching another unreadable one.
            kept[name] = {"present": True, "unreadable": str(unreadable)[:120]}

    # The key directory beside it, by NAMES and sizes. Its contents are key material.
    if secret.is_dir():
        kept[".secret"] = {
            "present": True,
            "files": sorted(
                [entry.name, entry.stat().st_size]
                for entry in secret.iterdir() if entry.is_file()),
        }
    else:
        kept[".secret"] = {"present": False}
    return {"version": 1, "kept": kept}


def differences(before: dict, after: dict) -> list:
    """Every class that did not come back the way it went in, named, in a fixed order.

    Reported per class rather than as one verdict on purpose: "the restore does not match" sends
    somebody looking through a tarball, and "sessions: 12 before, 0 after" sends them to the one
    thing that is wrong.
    """
    was = (before or {}).get("kept") or {}
    now = (after or {}).get("kept") or {}
    said = []
    for name in sorted(set(was) | set(now)):
        mine, theirs = was.get(name), now.get(name)
        if mine is None:
            said.append("%s: not in the manifest this backup was taken with, but present now"
                        % name)
            continue
        if theirs is None:
            said.append("%s: was backed up and is not in the restored state at all" % name)
            continue
        if mine == theirs:
            continue
        if mine.get("present") and not theirs.get("present"):
            said.append("%s: was there and is gone" % name)
        elif theirs.get("unreadable"):
            said.append("%s: came back unreadable (%s)" % (name, theirs["unreadable"]))
        elif "count" in mine or "count" in theirs:
            said.append("%s: %s record(s) before, %s after"
                        % (name, mine.get("count", "?"), theirs.get("count", "?")))
        else:
            said.append("%s: the contents changed" % name)
    return said


def unaccounted_files(state_dir) -> list:
    """Files a gateway wrote that this drill does not know how to check.

    The completeness half. A file in neither table is a file whose loss a restore would not
    report, and the right answer is to add it to one of them rather than to widen this.
    """
    root = Path(state_dir)
    known = set(everything_a_gateway_keeps())
    strangers = []
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.name.endswith((".lock", ".new", ".tmp")):
            continue
        if path.name in known:
            continue
        strangers.append(path.name)
    return strangers


def _as_text(manifest: dict) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True)


def main(argv=None) -> int:
    """`python -m agentnode_sdk.gateway.backup write|compare <state> [<manifest>]`.

    The shell script's half of this. Kept here rather than written twice in bash: counting
    records in a JSON-lines file with `wc` and hoping is how a drill comes to pass on a file
    that was truncated to exactly the right number of bytes.
    """
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print("usage: backup.py write|compare|unaccounted <state-dir> [<manifest>]")
        return 2
    verb, state = argv[0], argv[1]
    where = argv[2] if len(argv) > 2 else os.path.join(state, "..", MANIFEST_NAME)

    if verb == "write":
        Path(where).write_text(_as_text(what_is_in_it(state)), encoding="utf-8")
        print("    wrote %s" % where)
        return 0
    if verb == "unaccounted":
        strangers = unaccounted_files(state)
        for name in strangers:
            print("    PROBLEM        : %s is stored here and this drill does not check it"
                  % name)
        return 1 if strangers else 0
    if verb == "compare":
        try:
            before = json.loads(Path(where).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print("    PROBLEM        : no manifest to compare against (%s)" % str(exc)[:120])
            return 1
        said = differences(before, what_is_in_it(state))
        for line in said:
            print("    PROBLEM        : " + line)
        if not said:
            kept = before.get("kept") or {}
            print("    contents       : %d class(es) came back exactly as they went in"
                  % sum(1 for v in kept.values() if v.get("present")))
        return 1 if said else 0
    print("unknown verb: %s" % verb)
    return 2


if __name__ == "__main__":                                    # pragma: no cover - a CLI
    raise SystemExit(main())
