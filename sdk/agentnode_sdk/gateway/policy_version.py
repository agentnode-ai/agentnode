"""Which operator policy this is, counted, so a record can name one without quoting it.

A digest says *which* policy. It does not say *which one of this gateway's policies*, and those
are different questions to the person reading a record months later:

* a digest is 64 characters that cannot be turned back into anything and cannot be compared to
  memory -- "was that the one before or after we opened the allowlist?" is unanswerable from it;
* a version is a small number that orders them, so two records can be seen to have been made
  under the same policy or under different ones, and a change between them can be looked up.

Both, therefore, and both inside everything that binds: the version is useless without the digest
(it names nothing), and the digest is unfriendly without the version (it orders nothing).

## What the version is, exactly

**The count of DISTINCT operator policies this gateway has ever had.** The first one is 1. A
policy edited to something it has been before gets its original number back, because it is the
same policy -- and a record saying "version 3" must mean one policy, not "the third time somebody
edited the file".

It is assigned automatically rather than set by an operator. A version an operator types is one
they can forget to change, and a policy that changed without its version changing is precisely
what the binding exists to make impossible.

## What it is not

It is not a sequence somebody can use to tell how busy this gateway is or to guess what a policy
contains. It is an ordinal over digests and nothing else.

Nothing here decides anything. It records an ordering, and the readiness gate decides.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from agentnode_sdk.gateway.filelock import ProcessLock

#: Where the ordering lives, inside the gateway's own directory.
VERSIONS_NAME = "operator-policy-versions.json"

#: What an unknown policy is numbered. Not zero and not one: zero would be indistinguishable
#: from "this field was not filled in", and one would claim to be the first policy.
UNKNOWN = -1


class VersionsUnreadable(OSError):
    """The ordering cannot be read, so no version is claimed for anything.

    Its own type because a caller must tell it from a file that is simply absent: absent means
    this gateway has never had a policy recorded, and unreadable means it has and cannot see
    which. Neither is an excuse to invent a number -- a record binding a version that was guessed
    is worse than one binding none, because it looks checked.
    """


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VersionsUnreadable(
            "this gateway cannot read which of its operator policies is which (%s)"
            % str(exc)[:120]) from exc
    if not isinstance(body, dict):
        raise VersionsUnreadable("the operator policy ordering is not an object")
    return body


def version_for(root: str | os.PathLike[str], digest: str) -> int:
    """The number of this policy, assigning the next one if it has never been seen.

    Assigning happens under a process lock, so two gateways sharing a directory cannot both
    decide they are next and give one number to two policies.
    """
    wanted = str(digest or "")
    if not wanted:
        return UNKNOWN
    path = Path(root) / VERSIONS_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with ProcessLock(path):
        body = _load(path)
        known = body.get(wanted)
        if isinstance(known, int) and known > 0:
            return known
        assigned = max([v for v in body.values() if isinstance(v, int)] + [0]) + 1
        body[wanted] = assigned
        _atomically(path, json.dumps(body, indent=2, sort_keys=True))
        return assigned


def known_version(root: str | os.PathLike[str], digest: str) -> int:
    """The number of this policy if it already has one, WITHOUT assigning one.

    For readers -- a verifier, a statement, a report being checked -- which must not create an
    ordering entry as a side effect of looking at one.
    """
    wanted = str(digest or "")
    if not wanted:
        return UNKNOWN
    known = _load(Path(root) / VERSIONS_NAME).get(wanted)
    return known if isinstance(known, int) and known > 0 else UNKNOWN


def _atomically(path: Path, text: str) -> None:
    """Beside and renamed over, with the bounded retry Windows needs. One implementation."""
    from agentnode_sdk.gateway.filelock import atomically

    atomically(path, text)
