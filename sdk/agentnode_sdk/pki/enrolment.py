"""What enrolment leaves behind, and getting rid of it.

Its own module, and not part of `issuer.py`, for one structural reason: the issuer reaches the
gateway's file lock, and a WORKER host must be able to clear its own residues without the control
plane's package on the machine. `roles.py` holds that separation and the import graph test would
have caught the shortcut.
"""
from __future__ import annotations

import os
from pathlib import Path

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


__all__ = ["ENROLMENT_RESIDUES", "forget_the_enrolment"]
