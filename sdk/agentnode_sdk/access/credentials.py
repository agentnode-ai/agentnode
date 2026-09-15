"""Where a program on somebody's own machine keeps its device token.

The browser does not have this problem: it is given a session in a cookie it cannot read. A
command-line tool has no such arrangement, so it holds the credential itself, and the question is
where.

A file with tight permissions is the usual answer and it is not a bad one -- the connection store
already writes with the mode set before the bytes, atomically, so a token is never briefly
world-readable. What it does not survive is the ordinary accidents: a backup that copies dotfiles,
a support request that says "paste your config", a synced home directory, a screen share over a
terminal. A token in a file is a token in every copy of that file.

So the operating system's own keyring is used when there is one. It is what the platform provides
for exactly this, it is unlocked with the account rather than with a file permission, and a backup
of a home directory does not carry it.

When there is no keyring, this does NOT quietly fall back to plaintext. It refuses, says what is
missing and what to do, and leaves the choice with the person -- because a fallback that happens
silently is a fallback nobody decided on, and the whole point of moving the token was that nobody
had decided where it was.

`AGENTNODE_CREDENTIALS=file` is the decision, made out loud. Continuous integration sets it; a
person is told they can.
"""
from __future__ import annotations

import os

#: The keyring entry everything here lives under.
SERVICE = "agentnode"
#: What somebody sets when they have looked at the trade and chosen the file.
SAY_SO = "AGENTNODE_CREDENTIALS"


class NoSafePlace(Exception):
    """There is no keyring, and nobody has said a file is acceptable."""


def _keyring():
    """The platform's keyring, or None. Never raises: not having one is an answer."""
    try:
        import keyring
        from keyring.errors import NoKeyringError
    except ImportError:
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:                                         # noqa: BLE001
        return None
    # A "fail" backend is what keyring returns when it found nothing usable. Treating it as a
    # keyring would mean every read and write raising from somewhere much less helpful.
    if backend is None or "fail" in type(backend).__module__.lower():
        return None
    del NoKeyringError
    return keyring


def where_it_would_go() -> str:
    """"keyring", "file", or "" when there is nowhere this is allowed to put it."""
    if _keyring() is not None:
        return "keyring"
    if (os.environ.get(SAY_SO) or "").strip().lower() == "file":
        return "file"
    return ""


def why_not() -> str:
    """What to tell somebody who has nowhere to put a credential."""
    return (
        "This machine has no keyring that AgentNode can use, so there is nowhere to keep your "
        "access safely.\n"
        "  * On a desktop, install one: `pip install keyring` usually finds the system's own.\n"
        "  * On a server or in CI, set %s=file to keep it in a file with tight permissions "
        "instead. That is a real choice with a real cost -- the token then travels in any backup "
        "of that directory -- which is why it is not made for you." % SAY_SO)


def keep(name: str, token: str) -> str:
    """Put a token somewhere. Returns where it went, or raises `NoSafePlace`.

    Returning "file" means the CALLER must write it; this never writes one itself, so there is
    exactly one piece of code that puts a credential on disk and it is the one that already knows
    how to do it safely.
    """
    ring = _keyring()
    if ring is not None:
        ring.set_password(SERVICE, name, token)
        return "keyring"
    if where_it_would_go() == "file":
        return "file"
    raise NoSafePlace(why_not())


def fetch(name: str) -> str:
    """The token for a connection, from the keyring. "" when it is not there."""
    ring = _keyring()
    if ring is None:
        return ""
    try:
        return ring.get_password(SERVICE, name) or ""
    except Exception:                                         # noqa: BLE001
        # A locked or broken keyring is not a reason to behave as though the credential were
        # absent AND not a reason to crash a command that may not need it.
        return ""


def forget(name: str) -> bool:
    ring = _keyring()
    if ring is None:
        return False
    try:
        ring.delete_password(SERVICE, name)
        return True
    except Exception:                                         # noqa: BLE001
        return False
