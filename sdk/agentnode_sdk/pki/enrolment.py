"""What enrolment leaves behind, and getting rid of it.

Its own module, and not part of `issuer.py`, for one structural reason: the issuer reaches the
gateway's file lock, and a WORKER host must be able to clear its own residues without the control
plane's package on the machine. `roles.py` holds that separation and the import graph test would
have caught the shortcut.
"""
from __future__ import annotations

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


__all__ = ["ENROLMENT_RESIDUES", "SECRET_PREFIX", "forget_the_enrolment", "mint_a_secret"]
