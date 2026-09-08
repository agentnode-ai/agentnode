"""Who else can read the gateway's own files.

The gateway directory holds the token hashes, the identity, the replay ledger, the pairing lockout
and the live pairing code's hash. Anyone who can read it can impersonate a paired client; anyone
who can write it can do considerably more. The directory is created owner-only, but "created
owner-only" and "still owner-only" are different claims, and only the second one matters at the
moment the gateway starts serving.

On POSIX this is checkable and is therefore checked, at startup, and a directory that is readable
or writable by anyone else stops the gateway rather than producing a warning nobody reads. The
refusal names the exact command that fixes it.

On Windows the POSIX mode is largely advisory and a real answer would mean interpreting ACLs. This
module does not pretend: it reports that the permissions could not be verified, and that is
surfaced rather than being rounded up to "secure". Saying "unverified" is honest, and the honest
answer is what lets someone decide whether they care. The gateway is not blocked there, because
blocking on a check that cannot run would only teach operators to disable it -- and the deployment
target for a gateway is Linux, where the check is real.
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DirectoryVerdict:
    """What was found, in terms a person can act on."""

    ok: bool
    verifiable: bool
    reason: str = ""
    remedy: str = ""

    def as_dict(self) -> dict:
        return {"ok": self.ok, "verifiable": self.verifiable,
                "reason": self.reason, "remedy": self.remedy}


_UNVERIFIABLE = (
    "file permissions cannot be checked on this platform, so it is not known whether other "
    "accounts on this machine can read the gateway's tokens"
)


class InsecureStateDirectory(Exception):
    """The gateway's own directory is readable or writable by someone else."""


def inspect_fd(fd: int, shown_as: str) -> DirectoryVerdict:
    """The same judgement, about an already-open descriptor.

    `EM3C-EXTERNAL-0004` found that checking a path and then opening it are two operations on two
    possibly-different objects: whatever the name referred to at the moment of the check need not
    be what the next open returns. Checking the descriptor that is about to be used removes the
    gap, because there is only one object and it is already held.
    """
    if os.name != "posix":
        return DirectoryVerdict(True, False, _UNVERIFIABLE, "")
    return _judge(os.fstat(fd), shown_as)


def inspect(root: str | os.PathLike[str]) -> DirectoryVerdict:
    """Look at the directory. Decide nothing; the caller decides what a verdict means."""
    path = Path(root)
    if not path.exists():
        return DirectoryVerdict(True, True, "", "")

    if os.name != "posix":
        return DirectoryVerdict(True, False, _UNVERIFIABLE, "")

    return _judge(path.stat(), str(path))


def _judge(info, shown_as: str) -> DirectoryVerdict:
    problems = []
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o077:
        who = []
        if mode & 0o070:
            who.append("the group")
        if mode & 0o007:
            who.append("everyone else")
        problems.append(" and ".join(who) + " can reach it")
    if info.st_uid != os.getuid():
        problems.append("it belongs to another user")

    if not problems:
        return DirectoryVerdict(True, True, "", "")

    return DirectoryVerdict(
        ok=False,
        verifiable=True,
        reason=(
            f"the gateway's files at {shown_as} are not private: " + ", ".join(problems) +
            f" (mode {oct(mode)}). They hold the access tokens for every paired client, the "
            "record of which jobs have run, and the pairing lockout."
        ),
        remedy=f"chmod 700 {shown_as}" + (
            f" && chown $(id -un) {shown_as}" if info.st_uid != os.getuid() else ""
        ),
    )


def require_private(root: str | os.PathLike[str]) -> DirectoryVerdict:
    """Raise unless the directory is private, or cannot be judged on this platform."""
    verdict = inspect(root)
    if verdict.ok:
        return verdict
    raise InsecureStateDirectory(
        verdict.reason + "\n\nThe gateway was not started. To fix it:\n  " + verdict.remedy
    )
