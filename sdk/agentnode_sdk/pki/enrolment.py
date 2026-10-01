"""What enrolment leaves behind, and getting rid of it.

Its own module, and not part of `issuer.py`, for one structural reason: the issuer reaches the
gateway's file lock, and a WORKER host must be able to clear its own residues without the control
plane's package on the machine. `roles.py` holds that separation and the import graph test would
have caught the shortcut.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

#: WHAT AN ENROLMENT SECRET LOOKS LIKE, and it is a prefix so that it can be told apart.
#:
#: It used to be 64 bare lowercase hex characters, which is exactly what a digest, a run id and a
#: device id look like -- and the log scrubber deliberately leaves bare hex alone so that those
#: three stay readable. So a secret that reached a log line stayed there. A review called that
#: what it is: a documented trade is not evidence that disclosure cannot happen.
#:
#: The fix is to make the secret recognisable rather than to make the scrubber guess. Nothing
#: compares the format -- the issuer keeps a digest of the string and compares digests -- so this
#: is a change to what is generated and to what the scrubber matches, and to nothing else.
SECRET_PREFIX = "agentnode-enrol-1."


def mint_a_secret() -> str:
    """A single-use enrolment secret, in a shape a log scrubber can recognise."""
    import secrets

    return SECRET_PREFIX + secrets.token_hex(32)


#: What enrolment leaves behind in a service's TLS directory, and what nothing needs afterwards.
#: `request.json` is the one that matters: it contains the one-shot secret in clear.
ENROLMENT_RESIDUES = ("secret", "request.json")


def forget_the_enrolment(tls_dir) -> list:
    """Remove the one-shot secret and the request that carries it. Returns what it removed.

    Called after a certificate is delivered, and again by the worker before it serves -- across
    two machines the delivery happens on the other one, and the copy that was carried by hand is
    the one still lying in the worker's directory.

    Only ever removes these two names, only when the pair the service actually uses is in place,
    and never raises: a residue that cannot be removed is worth a line in a log, not a service
    that will not start. `preflight` REPORTS them and removes nothing, because a check that
    changes what it is checking is not a check.
    """
    folder = Path(tls_dir)
    if not ((folder / "cert.pem").is_file() and (folder / "key.pem").is_file()):
        return []
    gone = []
    for name in ENROLMENT_RESIDUES:
        residue = folder / name
        try:
            if residue.is_file():
                # The secret is written 0400, and a read-only file cannot be unlinked on
                # Windows: PermissionError, which is an OSError and which the `except` below
                # would have swallowed. The first version did exactly that -- it left the secret
                # sitting there and reported that it had removed nothing, which is at least
                # honest and is not the job. The test caught it.
                try:
                    os.chmod(residue, 0o600)
                except OSError:                               # pragma: no cover
                    pass
                residue.unlink()
                gone.append(name)
        except OSError:                                       # pragma: no cover - reported, not fatal
            pass
    return gone


def digest_of_a_secret(value: str) -> str:
    """The one way a secret's value is turned into the digest the inventory stores.

    It exists so the issuer and this module cannot drift: `Issuer.enroll` records
    `sha256(presented.strip())` as the consumption, and the check below has to ask the same
    question of a file's contents or the comparison is meaningless.
    """
    return hashlib.sha256(str(value).strip().encode("ascii", "replace")).hexdigest()


# A secret is 82 characters and a request a few hundred. Anything larger is not a residue this
# product wrote, and reading it unbounded is how a cleanup gets turned into a denial of service by
# a named pipe or a multi-gigabyte file.
MOST_A_RESIDUE_CAN_BE = 1 << 16


def _the_value_in(raw: str, name: str) -> str:
    """The secret value a residue's text carries, or "" if it carries none.

    `secret` holds the value itself. `request.json` carries it in a field, because the request is
    what the service hands back and the secret travels with it.
    """
    if name != "request.json":
        return raw
    try:
        body = json.loads(raw)
    except (ValueError, TypeError):
        return ""
    if not isinstance(body, dict):
        return ""
    value = body.get("secret")
    return value if isinstance(value, str) else ""


def _remove_if_it_is_spent(residue: Path, name: str, spent: set) -> bool:
    """Hash a residue and remove it only if it is one of `spent` -- through ONE open handle.

    The previous version read the file by pathname and then unlinked that pathname, which review
    rated CRITICAL: between the two the name can be made to point at something else, so the file
    that was checked need not be the file that is deleted. Content authorisation fixed every static
    state -- shared directories, junctions, late directories -- and did nothing about that gap.

    So the file is opened ONCE and everything is decided about that handle:

      * `O_NOFOLLOW`, where the platform has it, so the final component is never a symlink the
        caller can retarget. Windows has no such flag; what it does have is that creating a file
        symlink needs a privilege, and a junction is a directory rather than a file.
      * `fstat` on the handle: a regular file, and no larger than a residue can be. A device or a
        FIFO is not read at all, which is also the answer to a cleanup that could be made to block.
      * the content is read FROM THE HANDLE, not from the name again, so the bytes that authorise
        the removal belong to one specific inode rather than to whatever the name meant at the time.
      * and immediately before the removal the name is `lstat`ed and compared back to that inode.
        Anything that has moved under the name is left alone.

    **What remains, stated rather than glossed.** Neither POSIX nor Windows has an "unlink this
    inode" call, and Windows will not unlink an open file at all, so the handle must be closed before
    the removal. Between the last identity comparison and the `unlink` there is therefore a window no
    implementation here can close. To use it an actor must create files inside the issuer's own
    state directory -- root-owned -- and that same actor can simply delete the secret directly, so
    winning the race grants no capability they did not already have. That is a threat boundary, not
    atomicity, and it is the honest claim. What the identity check does buy is that the window is now
    between two syscalls rather than spanning a read, a parse and a hash.
    """
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(residue, flags)
    except OSError:
        return False                       # absent, a symlink, a directory, or not ours to read
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            return False
        if opened.st_size > MOST_A_RESIDUE_CAN_BE:
            return False
        raw = os.read(fd, MOST_A_RESIDUE_CAN_BE).decode("ascii", "replace")
    except OSError:                                           # pragma: no cover - unreadable
        return False
    finally:
        # The handle is closed before the removal because WINDOWS WILL NOT UNLINK AN OPEN FILE: the
        # first version of this held it open across the unlink, every removal failed with a sharing
        # violation, and the whole suite went red at once. Measured, not reasoned about.
        os.close(fd)

    if digest_of_a_secret(_the_value_in(raw, name)) not in spent:
        return False                       # not a secret this entry spent -- leave it where it is
    try:
        # The file that was hashed must be the file that is removed. `raw` came from the handle, so
        # it belongs to a specific inode; this asks whether the NAME still refers to that inode.
        here = os.stat(residue, follow_symlinks=False)
        if (here.st_dev, here.st_ino) != (opened.st_dev, opened.st_ino):
            return False                   # the name moved; whatever is there now is not ours
        try:
            os.chmod(residue, 0o600)       # written 0400, and Windows will not unlink read-only
        except OSError:                                       # pragma: no cover
            pass
        os.unlink(residue)
        return True
    except OSError:                                           # pragma: no cover - reported, not fatal
        return False


def forget_a_spent_secret(folder, spent_digests) -> list:
    """Remove a one-shot secret the ISSUER has already recorded as consumed. Returns what it
    removed.

    **A residue is removed only when its own CONTENT hashes to one of `spent_digests`** -- the
    digests the inventory records as consumed for the entry this directory belongs to. The
    decision is about the bytes in the file, not about the path that led to it.

    That is the fourth design of this check and the first one that is safe, so the three it
    replaces are worth stating. All of them asked "is this the right DIRECTORY?" and then removed
    by pathname:

      1. per-entry only. Broke when two entries were pointed at one directory: the spent entry's
         cleanup deleted the live entry's secret.
      2. plus "refuse directories a live entry delivers into", compared as strings. Broke on a
         symlink or a Windows junction: two spellings of one directory, unequal strings, and the
         cleanup walked through the alias. Rated CRITICAL by review.
      3. plus filesystem identity, `(st_dev, st_ino)`. Still broke, two ways: a directory absent
         when the live set was sampled contributed a *string* key while the same directory present
         later produced a *tuple*, so it could not match itself; and an alias could be created or
         retargeted between the comparison and the unlink, which no amount of comparing fixes
         because the unlink still followed a mutable name.

    Asking about content ends that series rather than extending it. Every one of those states --
    aliases, junctions, case differences, dot-dot, mounts, a directory that appears late, a link
    retargeted mid-operation -- can at worst present this function with a file whose content is a
    SPENT secret, and deleting a spent secret is precisely the intent. A live secret has a
    different value, so it hashes to something not in `spent_digests` and is never touched. The
    race that was destructive becomes benign, because the thing being checked can no longer
    disagree with the thing being acted on.

    `spent_digests` empty means nothing is known to be spent, so nothing is removed.

    THIS IS NOT `forget_the_enrolment`, and the difference is the defect it repairs.

    That function refuses to act until `cert.pem` AND `key.pem` are both in the directory,
    which is right on the SERVICE's host: there the secret is what buys a usable pair, and
    removing it before the pair exists would strand a service with no way back. On the ISSUER's
    host a `key.pem` never appears -- the private key stays with whoever requested the
    certificate and never crosses -- so that guard can never be satisfied there. The issuer
    therefore called it after every single issuance and it removed nothing, every time, while
    its own comment said the removal was "a property the product has" rather than a procedure
    somebody may follow. Found by enumerating the worker's and the control plane's disks in the
    acceptance run of 2026-09-30, criterion X6-F.

    The precondition here is a different one and it is the CALLER's to establish: the
    consumption is already committed to the inventory, so the secret is spent whatever else is
    or is not on disk, and a copy of it is worth nothing to its holder and something to an
    attacker. Callers must therefore commit first and call this second -- a crash in between
    leaves a spent plaintext whose replay is already refused, which is the safe way round.

    Removes only the two names in ENROLMENT_RESIDUES, never raises, and is safe to call again
    on a directory that has already been cleaned or no longer exists.
    """
    folder = Path(folder)
    spent = {d for d in (spent_digests or ()) if d}
    gone = []
    if not spent:
        return gone
    for name in ENROLMENT_RESIDUES:
        if _remove_if_it_is_spent(folder / name, name, spent):
            gone.append(name)
    return gone


__all__ = ["ENROLMENT_RESIDUES", "SECRET_PREFIX", "digest_of_a_secret",
           "forget_a_spent_secret", "forget_the_enrolment", "mint_a_secret"]
