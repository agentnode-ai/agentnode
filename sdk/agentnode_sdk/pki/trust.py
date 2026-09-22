"""What a service knows, at one moment, about time and revocation -- read from root's files.

Three files, all root's, all read-only to the services:

    the anchor            /etc/agentnode/trust/ca.pem         who issues
    the revocation list   /etc/agentnode/trust/revoked.crl    whom that issuer has taken back
    the floor             /var/lib/agentnode-floor/<role>.floor   how far time has certainly come

A `TrustView` holds their BYTES as they were when it was read, and JUDGES them when it is asked:
the floor's age against the monotonic clock of that moment, the list's expiry against the
effective time of that moment. So a view read a few seconds ago still gives a correct answer now
-- which is what lets a service re-evaluate its open connections from a view it reloads on a
fixed interval rather than on every pass (`worker/tls.py`, `Watch`).

Nothing here writes, and nothing here has a default for a file it could not read: a floor it
cannot trust is a refusal (`PeerRefused` with the floor's reason), and so is a list it cannot
believe. There is no way to build a view that skips either -- `pki.identity.check_peer` takes one
and has no default.
"""
from __future__ import annotations

import os
from pathlib import Path

from agentnode_sdk.pki import floor as _floor
from agentnode_sdk.pki import identity as _identity
from agentnode_sdk.pki import revocation as _revocation


def _bytes(path) -> tuple[bytes | None, str]:
    """(contents, "") or (None, why). Missing and unreadable are told apart for the floor."""
    try:
        with open(path, "rb") as handle:
            return handle.read(), ""
    except FileNotFoundError:
        return None, "missing"
    except OSError as exc:
        return None, type(exc).__name__


class TrustView:
    def __init__(self, *, anchor: bytes | None, revocation_list: bytes | None,
                 floor: bytes | None, floor_problem: str, role: str) -> None:
        self._anchor_bytes = anchor
        self._list_bytes = revocation_list
        self._floor_bytes = floor
        self._floor_problem = floor_problem
        self.role = role
        self._anchor = None

    @classmethod
    def read(cls, *, anchor, revocation_list, floor, role: str) -> "TrustView":
        """All three files, now. The anchor's absence surfaces when it is asked for."""
        anchor_bytes, _ = _bytes(anchor)
        list_bytes, _ = _bytes(revocation_list)
        floor_bytes, floor_problem = _bytes(floor)
        return cls(anchor=anchor_bytes, revocation_list=list_bytes, floor=floor_bytes,
                   floor_problem=floor_problem, role=role)

    @staticmethod
    def stamp(*paths) -> tuple:
        """What changes when any of the files changes -- for a watcher deciding to reload."""
        out = []
        for path in paths:
            try:
                st = os.stat(path)
                out.append((st.st_ino, st.st_mtime_ns, st.st_size))
            except OSError:
                out.append(None)
        return tuple(out)

    # ------------------------------------------------------------------ the judgements

    def anchor(self):
        if self._anchor is None:
            from cryptography import x509

            try:
                self._anchor = x509.load_pem_x509_certificate(self._anchor_bytes or b"")
            except Exception as exc:                          # noqa: BLE001
                raise _identity.PeerRefused(_identity.CHECK_VALIDITY, "",
                                            "this side cannot read its own trust anchor") from exc
        return self._anchor

    def effective_time(self, presented: str = "") -> float:
        """The later of the system clock and the floor -- or a refusal naming why there is no
        floor this side may use. Judged NOW, with the clocks of this moment."""
        if self._floor_bytes is None and self._floor_problem not in ("", "missing"):
            raise _identity.PeerRefused(_floor.UNREADABLE, presented,
                                        "the floor file could not be read (%s)"
                                        % self._floor_problem)
        try:
            value = _floor.judge(self._floor_bytes, role=self.role, boot=_floor._boot(),
                                 monotonic_now=_floor._monotonic())
        except _floor.FloorUnusable as unusable:
            raise _identity.PeerRefused(unusable.check, presented, unusable.detail) from unusable
        return _floor.effective_time(value)

    def revoked_serials(self, effective_time: float, presented: str = "") -> frozenset:
        """The serials in a list this side can believe at `effective_time` -- or a refusal naming
        why there is none. No list is never an empty list."""
        try:
            return _revocation.read(self._list_bytes, self.anchor(), effective_time).serials
        except _revocation.ListUnusable as unusable:
            raise _identity.PeerRefused(unusable.check, presented, unusable.detail) from unusable


def paths_of(settings) -> tuple[Path, Path, Path]:
    return Path(settings.anchor), Path(settings.revocation_list), Path(settings.floor)


__all__ = ["TrustView", "paths_of"]
