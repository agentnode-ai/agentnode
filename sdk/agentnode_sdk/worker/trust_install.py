"""A worker takes a fresher revocation list and tombstone list from its control plane.

Target-router decision Q-TGT-0001 D11/D12, resolution R3. Until now both files were copied to a
worker host by hand and expired after seven days, and a fail-closed worker with an expired list
refuses everything. This is the one way they arrive now: over the mutually authenticated, MAC'd
worker channel, as the `install_trust` request.

Nothing here trusts the channel for the CONTENT. Both documents are signed by the deployment's
CA, and each is verified against THIS worker's own anchor before anything is written:

  * the revocation list must be signed by the anchor, valid at this side's effective time, and
    must not carry a LOWER list number than the one installed (an equal number is accepted only
    when the bytes are identical -- two different lists with one number is equivocation);
  * the tombstone list must be signed by the anchor, be for this worker's deployment, be valid at
    the effective time, must not be OLDER than the one installed, and must not SHRINK: an identity
    once withdrawn stays in every later list.

Both are written durably (temporary file, fsync, rename) next to the files the TLS door already
reads, which it re-reads within its reload interval. The answer names what is installed now, so
the control plane can record which trust generation this worker acknowledged.
"""
from __future__ import annotations

from pathlib import Path

from agentnode_sdk.pki import files as _files
from agentnode_sdk.pki import revocation as _revocation
from agentnode_sdk.pki import tombstones as _tombstones


class TrustRefused(Exception):
    """The offered lists are not installed. `reason` is a stable code; nothing was written."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason + (": " + detail if detail else ""))
        self.reason = reason
        self.detail = detail


def _current(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def install(settings, revocation_list: bytes, identity_tombstones: bytes, *, role: str = "worker",
            files=None) -> dict:
    """Verify both offered documents against this side's own trust, then install both.

    `settings` is the side's `TlsSettings`. Returns `{"revocation_list_number", "tombstones_published_at",
    "installed": bool}`; `installed` is False when both offered documents were already in place.
    """
    if not settings.revocation_list or not settings.identity_tombstones:
        raise TrustRefused("not_configured", "this side keeps no revocation list or no tombstone list")
    files = files or _files.Files()
    view = settings.trust(role)
    try:
        anchor = view.anchor()
        effective = view.effective_time()
    except Exception as exc:                                  # noqa: BLE001 - every one refuses
        raise TrustRefused("own_trust_unusable", type(exc).__name__) from exc

    try:
        offered_list = _revocation.read(revocation_list, anchor, effective)
    except _revocation.ListUnusable as exc:
        raise TrustRefused("revocation_list_" + exc.check, exc.detail) from exc
    list_path = Path(settings.revocation_list)
    current_list = _current(list_path)
    current_number = _revocation.number_of(current_list)
    if current_number is not None:
        if offered_list.number < current_number:
            raise TrustRefused("revocation_list_older",
                               "number %d is below the installed %d" % (offered_list.number, current_number))
        if offered_list.number == current_number and revocation_list != current_list:
            raise TrustRefused("revocation_list_equivocation",
                               "a different list with the installed number %d" % current_number)

    try:
        offered_tombs = _tombstones.read(identity_tombstones, deployment=settings.deployment,
                                         verifier=view._verify_with_anchor)
        offered_tombs.usable_at(effective)
    except _tombstones.ListUnusable as exc:
        raise TrustRefused("tombstones_" + exc.reason, exc.detail) from exc
    tomb_path = Path(settings.identity_tombstones)
    current_tombs_raw = _current(tomb_path)
    if current_tombs_raw is not None:
        try:
            current_tombs = _tombstones.read(current_tombs_raw, deployment=settings.deployment,
                                             verifier=view._verify_with_anchor)
        except _tombstones.ListUnusable:
            current_tombs = None                              # an unreadable one is replaced
        if current_tombs is not None:
            if offered_tombs.published_at < current_tombs.published_at:
                raise TrustRefused("tombstones_older", "published before the installed list")
            if not current_tombs.uris <= offered_tombs.uris:
                raise TrustRefused("tombstones_shrink",
                                   "%d withdrawn identities would disappear"
                                   % len(current_tombs.uris - offered_tombs.uris))

    changed = (revocation_list != current_list) or (identity_tombstones != current_tombs_raw)
    if changed:
        # The tombstones first: a list of withdrawn identities can only add refusals, so a crash
        # between the two writes leaves this side stricter, never looser.
        _files.durable_replace(files, tomb_path, identity_tombstones, mode=0o644, label="trust-tombstones")
        _files.durable_replace(files, list_path, revocation_list, mode=0o644, label="trust-list")
    return {"revocation_list_number": offered_list.number,
            "revocation_list_next_update": offered_list.next_update,
            "tombstones_published_at": offered_tombs.published_at,
            "tombstones_not_after": offered_tombs.not_after,
            "installed": bool(changed)}


__all__ = ["TrustRefused", "install"]
