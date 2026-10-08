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
import time
from pathlib import Path

from agentnode_sdk.pki import floor as _floor
from agentnode_sdk.pki import identity as _identity
from agentnode_sdk.pki import revocation as _revocation


def _sha256():
    from cryptography.hazmat.primitives import hashes

    return hashes.SHA256()


#: HOW MANY TIMES A READ THAT FAILED FOR A REASON OTHER THAN ABSENCE IS ASKED AGAIN, and how long
#: is waited between the tries. Both are constants of this module on purpose: a bound that a caller
#: could pass in is not a bound, and one derived from a clock reading is not fixed.
#:
#: WHY ANY RETRY AT ALL. These files belong to root and are replaced by root's own run while the
#: services read them: `pki/files.durable_publish` writes a stage, fsyncs it, and `os.replace`s it
#: onto the target. `os.replace` is atomic and `issuer._publish_list` reports a revocation effective
#: only once that promotion is durable, so there is never a half-written file to read. A READER,
#: though, can still catch an error from the operating system while the swap happens -- measured on
#: one platform at hundreds of exceptions against a couple of thousand durable publications, with no
#: absent and no empty read among them. Asking again is the right answer to that; believing the file
#: would not be. After these tries this side still refuses, so nothing is weakened.
#:
#: AND ABSENCE IS NOT RETRIED. A file that is not there will not appear by being asked for again, and
#: a deployment that has published no list is a different state from one whose list could not be
#: read -- which is the whole subject of this repair.
_TRIES = 4
_BETWEEN_TRIES = 0.02
#: The most that can be spent waiting, by construction rather than by measurement.
_RETRY_CEILING_SECONDS = (_TRIES - 1) * _BETWEEN_TRIES


def _bytes(path, *, tries: int = 1) -> tuple[bytes | None, str]:
    """(contents, "") or (None, why). Missing and unreadable are told apart, for the floor AND for
    the revocation list.

    `tries` DEFAULTS TO ONE so that every caller that had this behaviour keeps it exactly: the
    anchor, the floor and the tombstones are read once, as before. Only the revocation list asks for
    more, because that is the file this repair is about. A read that fails for any reason other than
    absence is asked again up to `tries` times with `_BETWEEN_TRIES` in between; absence returns at
    once. Whatever happens, this returns either the WHOLE contents or None with the reason -- there
    is no partial result and nothing from an earlier call is remembered.
    """
    why = ""
    for attempt in range(max(1, int(tries))):
        try:
            with open(path, "rb") as handle:
                return handle.read(), ""
        except FileNotFoundError:
            return None, "missing"
        except OSError as exc:
            why = type(exc).__name__
            if attempt + 1 < max(1, int(tries)):
                time.sleep(_BETWEEN_TRIES)
    return None, why


class TrustView:
    def __init__(self, *, anchor: bytes | None, revocation_list: bytes | None,
                 floor: bytes | None, floor_problem: str, role: str, identity: str = "",
                 identity_tombstones: bytes | None = None,
                 tombstones_required: bool = False, list_problem: str = "") -> None:
        self._anchor_bytes = anchor
        self._list_bytes = revocation_list
        #: WHY THIS IS KEPT NOW. It was read and thrown away, so a list that could not be READ was
        #: reported as a list that was not THERE -- one sentence for two states that send a reader to
        #: different places: "publish a list" against "find out what is holding the file". The floor
        #: has kept its reason since a floor it cannot trust became a refusal; this is the list doing
        #: the same. The default is empty so that every existing construction of this class is
        #: unchanged.
        self._list_problem = list_problem
        self._floor_bytes = floor
        self._floor_problem = floor_problem
        self.role = role
        #: Who this side is, from its own certificate. The floor is judged against it, so that a
        #: floor copied from the other machine -- same role, same format, perfectly fresh in the
        #: boot it was written in -- is refused instead of believed. Empty means "not told", and
        #: that is itself a refusal rather than a pass.
        self.identity = identity
        self._anchor = None
        #: The signed list of identities that may never be accepted again. Optional on one
        #: machine, where the issuer's own inventory is the authority and is right here;
        #: REQUIRED once a verifier is on another machine and has no inventory to consult.
        self._tombstone_bytes = identity_tombstones
        self._tombstones_required = bool(tombstones_required)

    @classmethod
    def read(cls, *, anchor, revocation_list, floor, role: str, identity: str = "",
             identity_tombstones=None, tombstones_required: bool = False) -> "TrustView":
        """Every file, now. The anchor's absence surfaces when it is asked for."""
        anchor_bytes, _ = _bytes(anchor)
        list_bytes, list_problem = _bytes(revocation_list, tries=_TRIES)
        floor_bytes, floor_problem = _bytes(floor)
        tombstone_bytes = None
        if identity_tombstones:
            tombstone_bytes, _ = _bytes(identity_tombstones)
        return cls(anchor=anchor_bytes, revocation_list=list_bytes, floor=floor_bytes,
                   floor_problem=floor_problem, role=role, identity=identity,
                   identity_tombstones=tombstone_bytes,
                   tombstones_required=tombstones_required, list_problem=list_problem)

    def withdrawn(self, effective_time: float, presented: str) -> frozenset:
        """The identities this deployment has permanently withdrawn, authenticated.

        A list that cannot be believed is a refusal, never an empty set -- "I could not read
        who is banned" is not "nobody is banned". Where no list is configured at all and none
        is required, there is nothing to check and nothing is withdrawn; that is a deployment
        that has not turned this on, which is different from one whose list went missing.
        """
        from agentnode_sdk.pki import tombstones as _tombstones

        if self._tombstone_bytes is None:
            if self._tombstones_required:
                raise _identity.PeerRefused(
                    _identity.CHECK_REVOKED, presented,
                    "this side requires the deployment's list of withdrawn identities and "
                    "could not read it. A verifier that cannot tell whether an identity was "
                    "withdrawn does not accept it.")
            return frozenset()
        signed = _tombstones.read(self._tombstone_bytes, deployment=self.deployment_of(presented),
                                  verifier=self._verify_with_anchor)
        signed.usable_at(effective_time)
        return signed.uris

    def deployment_of(self, presented: str) -> str:
        """Which deployment a presented URI claims. The list is checked against the same one
        the certificate names, so a list for another deployment is refused rather than used."""
        try:
            return _identity.parse(presented).deployment
        except Exception:                                     # noqa: BLE001
            return ""

    def _verify_with_anchor(self, body: bytes, signature: bytes) -> None:
        """Verify against the CA's public key -- the same anchor certificates chain to."""
        from cryptography.hazmat.primitives.asymmetric import ec

        self.anchor().public_key().verify(signature, body, ec.ECDSA(_sha256()))

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
            value = _floor.judge(self._floor_bytes, role=self.role, identity=self.identity,
                                 boot=_floor._boot(), monotonic_now=_floor._monotonic())
        except _floor.FloorUnusable as unusable:
            raise _identity.PeerRefused(unusable.check, presented, unusable.detail) from unusable
        return _floor.effective_time(value)

    def revoked_serials(self, effective_time: float, presented: str = "") -> frozenset:
        """The serials in a list this side can believe at `effective_time` -- or a refusal naming
        why there is none. No list is never an empty list."""
        # A LIST THAT COULD NOT BE READ SAYS SO, rather than saying there is none. `_bytes` tells
        # the two apart and this is where the distinction was being dropped. Both are refusals and
        # neither is softened: the only difference is which of the two true things is said. The
        # wording is the one `revocation.load` already uses for the path it reads itself, so an
        # operator meets one sentence for this state and not two.
        if self._list_bytes is None and self._list_problem not in ("", "missing"):
            raise _identity.PeerRefused(
                _revocation.UNREADABLE, presented,
                "the revocation list could not be read (%s)" % self._list_problem)
        try:
            return _revocation.read(self._list_bytes, self.anchor(), effective_time).serials
        except _revocation.ListUnusable as unusable:
            raise _identity.PeerRefused(unusable.check, presented, unusable.detail) from unusable


def paths_of(settings) -> tuple[Path, Path, Path]:
    return Path(settings.anchor), Path(settings.revocation_list), Path(settings.floor)


__all__ = ["TrustView", "paths_of"]
