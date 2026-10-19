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
the control plane can record which trust generation this worker acknowledged: it is computed by
READING BACK the installed files and verifying them again (trust-refresh C10), never from what was
offered, and it carries the exact identity of the pair (`FACT_FIELDS`), digests included.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from agentnode_sdk.pki import files as _files
from agentnode_sdk.pki import revocation as _revocation
from agentnode_sdk.pki import tombstones as _tombstones

#: The exact identity of an installed trust pair. An acknowledgement repeats every one of them, and
#: the control plane admits a target only while all of them equal its own pair (trust-refresh C11/C12).
FACT_FIELDS = ("revocation_list_number", "revocation_list_sha256", "revocation_list_next_update",
               "tombstones_sha256", "tombstones_published_at", "tombstones_not_after")

#: A list published "later" than this side's effective time by more than the clocks can disagree
#: is not yet valid here, and is refused at install.
MAX_SKEW_SECONDS = 300.0

_INSTALL_LOCK = threading.Lock()


def facts_of(revocation_list: bytes, identity_tombstones: bytes) -> dict:
    """The exact identity of a trust pair, PARSED ONLY: one parser for the worker's read-back and the
    control plane's own pair, so equal bytes always give equal facts. Callers judge signatures where
    they rely on them. Raises ValueError when either document does not parse."""
    number = _revocation.number_of(revocation_list)
    if number is None:
        raise ValueError("the revocation list does not parse")
    try:
        from cryptography import x509

        crl = x509.load_pem_x509_crl(revocation_list)
        next_update = _revocation._stamp(crl, "next_update")
        serials = sorted(_revocation.serial_text(r.serial_number) for r in crl)
        body = json.loads(identity_tombstones.decode("utf-8"))["body"]
        published_at, not_after = float(body["published_at"]), float(body["not_after"])
        content = {"deployment": str(body["deployment"]), "revoked_serials": serials,
                   "withdrawn_identities": sorted(str(u) for u in body.get("uris") or ())}
    except Exception as exc:                                  # noqa: BLE001 - one answer: does not parse
        raise ValueError("the trust pair does not parse (%s)" % type(exc).__name__) from exc
    return {"revocation_list_number": int(number),
            "revocation_list_sha256": hashlib.sha256(revocation_list).hexdigest(),
            "revocation_list_next_update": float(next_update),
            "tombstones_sha256": hashlib.sha256(identity_tombstones).hexdigest(),
            "tombstones_published_at": published_at,
            "tombstones_not_after": not_after,
            # WHAT the pair refuses, without when it was signed: two pairs with equal content refuse the
            # same certificates and identities. Not an acknowledgement field.
            "content_sha256": hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()}


def differences(ack: dict | None, expected: dict | None) -> list[str]:
    """The fact fields in which an acknowledgement is not EXACTLY the expected pair (all of them when
    either side is missing). Empty means identical."""
    if not ack or not expected:
        return list(FACT_FIELDS)
    out = []
    for field in FACT_FIELDS:
        if field not in ack or field not in expected:
            out.append(field)
            continue
        a, e = ack[field], expected[field]
        try:
            same = (str(a) == str(e)) if field.endswith("_sha256") else (
                int(a) == int(e) if field == "revocation_list_number" else float(a) == float(e))
        except (TypeError, ValueError):
            same = False
        if not same or isinstance(a, bool):
            out.append(field)
    return out


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

    `settings` is the side's `TlsSettings`. Returns the `FACT_FIELDS` of the pair READ BACK from disk
    and verified again, plus `"installed": bool` (False when both offered documents were already in
    place). A failure after the tombstones were replaced leaves this side stricter (a list of
    withdrawn identities only adds refusals) and is never answered as installed. One install at a
    time: the check against what is installed and the write are one step.
    """
    with _INSTALL_LOCK:
        return _install(settings, revocation_list, identity_tombstones, role=role, files=files)


def _install(settings, revocation_list: bytes, identity_tombstones: bytes, *, role: str, files) -> dict:
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
    if offered_list.this_update > effective + MAX_SKEW_SECONDS:
        raise TrustRefused("revocation_list_not_yet_valid", "published after this side's effective time")
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
    if offered_tombs.published_at > effective + MAX_SKEW_SECONDS:
        raise TrustRefused("tombstones_not_yet_valid", "published after this side's effective time")
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
        # A temporary file an interrupted install left behind would make every later one fail
        # (the stage is created exclusively) -- and an install that can never succeed again is a
        # list that expires. The door never reads these names, so they are cleared first.
        for path in (tomb_path, list_path):
            stale = path.parent / ("." + path.name + ".tmp")
            if files.exists(stale):
                files.remove(stale)
        # The tombstones first: a list of withdrawn identities can only add refusals, so a crash
        # between the two writes leaves this side stricter, never looser.
        _files.durable_replace(files, tomb_path, identity_tombstones, mode=0o644, label="trust-tombstones")
        _files.durable_replace(files, list_path, revocation_list, mode=0o644, label="trust-list")
    return dict(installed_facts(settings, view, list_path, tomb_path, effective), installed=bool(changed))


def installed_facts(settings, view, list_path: Path, tomb_path: Path, effective: float) -> dict:
    """What is installed NOW: both files read back, verified against this side's anchor again, and
    described. Anything short of that is a refusal, never an acknowledgement."""
    held_list, held_tombs = _current(list_path), _current(tomb_path)
    if held_list is None or held_tombs is None:
        raise TrustRefused("installed_unreadable", "an installed trust file could not be read back")
    try:
        _revocation.read(held_list, view.anchor(), effective)
        _tombstones.read(held_tombs, deployment=settings.deployment,
                         verifier=view._verify_with_anchor).usable_at(effective)
        return facts_of(held_list, held_tombs)
    except (_revocation.ListUnusable, _tombstones.ListUnusable, ValueError) as exc:
        raise TrustRefused("installed_unverifiable", type(exc).__name__) from exc


__all__ = ["FACT_FIELDS", "MAX_SKEW_SECONDS", "TrustRefused", "differences", "facts_of", "install",
           "installed_facts"]
