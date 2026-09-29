"""A side's own time floor, established and kept with what that side is allowed to hold.

WHY THIS MODULE EXISTS. Until the first two-machine run there was one way to set a floor up and
keep it: `Issuer.floor_init` and `Issuer.tick`. Both live on the issuer, and the issuer needs the
CA's private key and its inventory. On one host that costs nothing -- the CA is on the same disk.
On two it is a contradiction, and the cross-host run of 2026-09-29 walked straight into it: the
worker refuses every connection without a floor, a floor must be made on the machine it is for
(it is keyed to that kernel's boot), and making one asked the worker for the one file a worker
must never have. The install stopped there.

Reading the code afterwards showed the dependency was incidental rather than essential:

    _key, ca_cert = self._ca()                 # issuer.py, floor_init
    not_before = ca_cert.not_valid_before_utc  # ... and the key is never used again

The only thing taken from the CA is the trust anchor's `notBefore`, and the anchor is public and
is already on the worker, because a side that could not read its own anchor could not verify
anybody. So the floor's whole lifecycle fits in a module that imports no issuer, reads no
inventory and opens no private key.

WHAT IS DELIBERATELY NOT HERE. Nothing that belongs to the issuer: no revocation list is
published, no overlap is ended, no entry is issued. A worker that cannot do those things is not
missing a capability; it is a worker.
"""
from __future__ import annotations

from pathlib import Path

from agentnode_sdk.pki import files as _files
from agentnode_sdk.pki import floor as _floor
from agentnode_sdk.pki import identity as _identity
from agentnode_sdk.pki import revocation as _revocation


class FloorRefused(Exception):
    """This machine will not set up or move its floor, and says which check stopped it."""


def _certificate(path):
    from cryptography import x509

    try:
        return x509.load_pem_x509_certificate(Path(path).read_bytes())
    except OSError as exc:
        raise FloorRefused("cannot read the certificate at %s (%s)"
                           % (path, type(exc).__name__)) from exc
    except Exception as exc:                                  # noqa: BLE001 - any parse failure
        raise FloorRefused("the file at %s is not a certificate" % path) from exc


def identity_in(certificate_path) -> str:
    """The identity URI this side's own certificate carries -- exactly one, or a refusal.

    Not the serial and not a fingerprint: those change when the certificate is renewed, and a
    floor that had to be rebuilt at every renewal would hand out a fresh tolerance each time.
    """
    sans = _identity._uri_sans(_certificate(certificate_path))
    if len(sans) != 1:
        raise FloorRefused("the certificate at %s carries %d URI names and an identity is "
                           "exactly one" % (certificate_path, len(sans)))
    try:
        return _identity.parse(sans[0]).uri()
    except _identity.NotAnIdentity as exc:
        raise FloorRefused("the certificate's URI name is not an identity (%s)" % exc) from exc


def anchor_not_before(anchor_path) -> float:
    """The lower bound a floor starts at: when the deployment's anchor began to be valid.

    Signed, deployment-wide, and the same number on every machine -- which is what makes it a
    bound rather than a reading of whatever clock happened to be set here.
    """
    certificate = _certificate(anchor_path)
    moment = getattr(certificate, "not_valid_before_utc", None)
    if moment is None:                                        # pragma: no cover - old library
        import datetime as _dt

        moment = certificate.not_valid_before.replace(tzinfo=_dt.timezone.utc)
    return moment.timestamp()


def _list_this_update(list_path, anchor_path, at: float):
    """`thisUpdate` of a signed revocation list this machine can believe, or None.

    A worker may raise its floor to a time the issuer signed. It may not raise it to a time it
    read off an unsigned file, so an unreadable or unverifiable list contributes nothing rather
    than contributing a guess.
    """
    if not list_path:
        return None
    try:
        data = Path(list_path).read_bytes()
    except OSError:
        return None
    try:
        return _revocation.read(data, _certificate(anchor_path), at).this_update
    except (_revocation.ListUnusable, FloorRefused):
        return None


def init(floor_dir, role: str, *, certificate, anchor, files=None,
         tolerance_s: float = _floor.DEFAULT_TOLERANCE_SECONDS,
         max_age_s: float = _floor.DEFAULT_MAX_AGE_SECONDS) -> str:
    """Set this side's floor up, once, from its own certificate and its anchor.

    A floor that exists and parses is never replaced: that would grant a second tolerance, and
    the whole point of the two counters is that a tolerance is spent once. Replacing an
    unreadable one is a decision somebody takes with `recover` or `adopt`, not something this
    does quietly.
    """
    if role not in _floor.ROLES:
        raise FloorRefused("a floor belongs to the gateway or the worker, not to %r" % role)
    handles = files or _files.Files()
    who = identity_in(certificate)
    parsed = _identity.parse(who)
    if parsed.role != role:
        raise FloorRefused("this certificate is the %s's and the floor asked for is the %s's"
                           % (parsed.role, role))

    directory = Path(floor_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = _floor.path_for(directory, role)
    _files.settle_stage(handles, path, _floor.newer, label="init-floor-" + role)
    if handles.exists(path):
        try:
            existing = _floor.parse(handles.read(path))
        except _floor.FloorUnusable as exc:
            raise FloorRefused(
                "there is a floor file here that does not parse. Starting a new one would start "
                "its counters again, which is a decision, not a repair: `agentnode pki floor "
                "adopt` carries a format-1 floor forward with its counters, and `--after-loss` "
                "on the issuer replaces one that is truly gone (%s)" % exc.detail) from None
        if existing.identity != who:
            raise FloorRefused(
                "the floor here belongs to %s and this side is %s. A floor is not shared and is "
                "not carried between machines; remove the wrong one deliberately if this machine "
                "really is being re-used." % (existing.identity, who))
        raise FloorRefused("the %s floor exists and is readable; setting it up again would grant "
                           "a second tolerance" % role)

    state = _floor.initial(role, anchor_not_before(anchor), who,
                           tolerance_s=tolerance_s, max_age_s=max_age_s)
    _files.durable_publish(handles, path, state.to_bytes(), mode=0o644,
                           label="init-floor-" + role)
    return str(path)


def settle_and_advance(floor_dir, role: str, *, certificate, anchor, revocation_list="",
                       files=None) -> dict:
    """The periodic run on a machine that is not the issuer: resolve, then move this floor on.

    The order is the issuer's, minus everything a worker may not do. What is left is exactly the
    part that keeps this side able to serve, and it fails LOUDLY: a floor that stops being
    written ages out and the side stops, which is the behaviour that is wanted -- but a run that
    could not write it must say so rather than exit as though it had.
    """
    handles = files or _files.Files()
    who = identity_in(certificate)
    path = _floor.path_for(Path(floor_dir), role)
    report = {"identity": who, "floor": str(path), "settled": "", "written": ""}

    report["settled"] = _files.settle_stage(handles, path, _floor.newer, label="settle-floor")

    try:
        state = _floor.parse(handles.read(path))
    except (OSError, _floor.FloorUnusable) as exc:
        raise FloorRefused("there is no floor here to move on (%s). `agentnode pki floor init` "
                           "starts one." % type(exc).__name__) from exc
    if state.identity != who:
        raise FloorRefused("the floor here belongs to %s and this side is %s"
                           % (state.identity, who))

    published = _list_this_update(revocation_list, anchor, _floor._system_now())
    new = _floor.advance(state, system_now=_floor._system_now(),
                         monotonic_now=_floor._monotonic(), boot=_floor._boot(),
                         list_this_update=published)
    _files.durable_publish(handles, path, new.to_bytes(), mode=0o644, label="floor-" + role)
    report["written"] = "generation %d" % new.generation
    report["floor_value"] = new.floor
    return report


def adopt(floor_dir, role: str, *, certificate, files=None) -> str:
    """Carry an existing format-1 floor forward under this side's identity, counters untouched.

    For a deployment upgrading into the format that names identities. Re-initialising would be
    the easy path and the wrong one: it starts both counters at zero and so hands out a fresh
    tolerance, which is the one thing the counters exist to prevent.
    """
    handles = files or _files.Files()
    who = identity_in(certificate)
    path = _floor.path_for(Path(floor_dir), role)
    try:
        data = handles.read(path)
    except OSError as exc:
        raise FloorRefused("there is no floor at %s to adopt (%s)"
                           % (path, type(exc).__name__)) from exc
    try:
        _floor.parse(data)
    except _floor.FloorUnusable:
        pass
    else:
        raise FloorRefused("the floor at %s already names an identity; there is nothing to adopt"
                           % path)
    state = _floor.adopt(data, who)
    if state.role != role:
        raise FloorRefused("that floor is the %s's, not the %s's" % (state.role, role))
    _files.durable_publish(handles, path, state.to_bytes(), mode=0o644, label="adopt-floor")
    return str(path)


__all__ = ["FloorRefused", "adopt", "anchor_not_before", "identity_in", "init",
           "settle_and_advance"]
