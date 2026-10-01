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


def _the_secret_inside(residue: Path, name: str) -> str:
    """The secret value a residue carries, or "" if it carries none that can be read.

    `secret` holds the value itself. `request.json` carries it in a field, because the request is
    what the service hands back and the secret travels with it.
    """
    try:
        raw = residue.read_text(encoding="ascii", errors="replace")
    except OSError:                                           # pragma: no cover - unreadable
        return ""
    if name != "request.json":
        return raw
    try:
        body = json.loads(raw)
    except (ValueError, TypeError):
        return ""
    return body.get("secret") or "" if isinstance(body, dict) else ""


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
        residue = folder / name
        try:
            if not residue.is_file():
                continue
            if digest_of_a_secret(_the_secret_inside(residue, name)) not in spent:
                continue                   # not a secret this entry spent -- leave it where it is
            # Written 0400, and a read-only file cannot be unlinked on Windows.
            try:
                os.chmod(residue, 0o600)
            except OSError:                                   # pragma: no cover
                pass
            residue.unlink()
            gone.append(name)
        except OSError:                                       # pragma: no cover - reported, not fatal
            pass
    return gone


__all__ = ["ENROLMENT_RESIDUES", "SECRET_PREFIX", "digest_of_a_secret",
           "forget_a_spent_secret", "forget_the_enrolment", "mint_a_secret"]
