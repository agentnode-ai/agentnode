"""Two worker services on ONE host: what must not overlap (target-router T20, Codex D6/R7).

A managed worker and a customer worker may share a worker host for a private test. That is two
identities and two services on one machine -- never physical separation, and nothing here claims
it. What CAN be checked is that the two do not share anything a worker's security rests on:
service account, listening port, identity, state directory, TLS material, trust view, keyring,
floor, journal, runtime directory or unit. `violations` names every overlap; the host test runs it
on the real configurations AND attempts the cross-reads empirically.
"""
from __future__ import annotations

import os
from typing import Any

PATH_KEYS = ("state_dir", "tls_dir", "keyring", "floor_dir", "journal_dir", "runtime_dir", "trust_dir")
UNIQUE_KEYS = ("user", "uid", "identity_uri", "unit")


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(p))))


def _inside(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep)


def violations(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """Every way service `a` and service `b` overlap. Empty means none of the checked ones do."""
    out = []
    for k in UNIQUE_KEYS:
        if a.get(k) is None or b.get(k) is None:
            out.append("%s is not declared for both services" % k)
        elif a[k] == b[k]:
            out.append("both services share %s %r" % (k, a[k]))
    if a.get("uid") == 0 or b.get("uid") == 0:
        out.append("a service runs as root; the customer worker must run as its own unprivileged account")
    la, lb = str(a.get("listen", "")), str(b.get("listen", ""))
    if la.rsplit(":", 1)[-1] == lb.rsplit(":", 1)[-1] and la.rsplit(":", 1)[0] == lb.rsplit(":", 1)[0]:
        out.append("both services listen on %s" % la)
    for ka in PATH_KEYS:
        for kb in PATH_KEYS:
            pa, pb = a.get(ka), b.get(kb)
            if not pa or not pb:
                continue
            if _inside(pa, pb) or _inside(pb, pa):
                out.append("%s %s and %s %s overlap" % (ka, pa, kb, pb))
    return sorted(set(out))


__all__ = ["PATH_KEYS", "UNIQUE_KEYS", "violations"]
