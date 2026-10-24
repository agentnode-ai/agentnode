"""FINDING-TFL-INSTALL-1: an install never rewinds the trust predecessor to an older legacy pair (Codex Q-TIL-0001,
profile trust-install-r1 C1-C22, DECISION-0001 R1-R13, DECISION-0002).

The legacy predecessor `targets/trust-previous/` is ordered against the pointer's generation P, the deployment's
pair C and the issuer's highest used number U by its ANCHOR-AUTHENTICATED list number -- never by its bytes --
under the publication lock; what was decided is moved to `trust-publication/legacy-retired/`, everything else is
refused by name with nothing changed. Real issuer, register, acks, journal and worker-side install (the lifecycle
stand); only the network is a double. Each test names the criterion / counter-check (CCn) it holds."""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from agentnode_sdk.pki import revocation as rl
from agentnode_sdk.pki import tombstones as tomb
from agentnode_sdk.remote_mcp import control_floor
from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.remote_mcp import trust_publication as TP
from agentnode_sdk.worker import trust_install as TI
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout

from .test_target_lifecycle import _provision, env  # noqa: F401 - the lifecycle stand


class Crash(BaseException):
    """A SIGKILL stand-in: nothing after it runs, no `except Exception` catches it."""


class Ledger:
    def __init__(self, fail: bool = False):
        self.events, self.fail = [], fail

    def append(self, kind, **fields):
        if self.fail:
            raise OSError("ledger unavailable")
        self.events.append(dict(fields, kind=kind))
        return {}


# ---------------------------------------------------------------------- the stand
def _trust(env) -> Path:
    return env["world"].root / "trust"


def _pair(env):
    return (_trust(env) / "revoked.crl").read_bytes(), (_trust(env) / "revoked-identities.json").read_bytes()


def _legacy(env) -> Path:
    return Path(env["reg"].stand) / "targets" / T.PREVIOUS_DIR


def _number(pair) -> int:
    return TI.facts_of(*pair)["revocation_list_number"]


def _pointer_number(env):
    return (TP.load_pointer(env["reg"].stand) or {}).get("revocation_list_number")


def _records(env):
    return TP.load_records(env["reg"].stand)


def _retired(env) -> list[Path]:
    d = TP.retired_dir(env["reg"].stand)
    return sorted(p for p in d.iterdir()) if d.is_dir() else []


def _prev(env):
    pair = T.previous_pair(env["reg"].stand)
    try:
        return TP.verify_pair(env["ops"], *pair) if pair else None
    except ValueError:
        return None


def _admitted(env, tid="managed-w1"):
    return T.trust_current(env["acks"].load().get(tid), now=time.time(), deployment=TI.facts_of(*_pair(env)),
                           previous=_prev(env))


def _rotated(env, monkeypatch):
    """Two rotations: a pointer P exists and C = P + 1, every live target exactly on C."""
    monkeypatch.setattr(TP, "ROTATE_AFTER_SECONDS", 0.0)
    assert env["ops"].refresh_trust()["lagging"] == []
    env["ops"].refresh_trust(force=True)
    assert _pointer_number(env) == _number(_pair(env)) - 1


def _lagging_rotation(env, tid="managed-w1"):
    """A rotation with `tid` unreachable: it holds the new pointer's pair exactly (admitted as predecessor)."""
    w = env["workers"][tid]
    real = w.install_trust
    w.install_trust = lambda crl, tombs: (_ for _ in ()).throw(OSError("worker unreachable"))
    try:
        report = env["ops"].refresh_trust(force=True)
    finally:
        w.install_trust = real
    assert tid in report["lagging"], report


def _ca(env):
    return env["world"]._ca()


def _forge(env, number, at, *, key=None, cert=None, deployment=None, tomb_at=None, tomb_key=None, uris=(),
           revoked=None):
    """A pair signed by the deployment's CA (or the given key/cert), with any number and times."""
    k, c = _ca(env)
    crl = rl.build(key or k, cert or c, revoked or {}, number=number, now=at)
    signer = tomb_key or key or k
    tombs = tomb.publish(deployment or env["world"].deployment, uris, now=at if tomb_at is None else tomb_at,
                         signer=lambda b: signer.sign(b, ec.ECDSA(hashes.SHA256())))
    return crl, tombs


def _scene(env, *, gap: int = 2, p_at: float | None = None, c_at: float | None = None):
    """A controlled stand: the issuer has used U numbers, the pointer selects a forged #U-gap signed at `p_at`, the
    deployment's pair is a forged #U signed at `c_at` -- room for a candidate between them in number and time."""
    now = float(int(time.time()))                             # whole seconds: a CRL's times have no fraction
    for _ in range(gap + 1):
        env["world"].issuer.publish()
    u = int(env["world"].issuer.inventory()["list_number"])
    p = _forge(env, u - gap, now - 4000 if p_at is None else p_at)
    rec = {"record_id": "r-000000000900", "from": TI.facts_of(*p)}
    TP._save_previous(env["ops"], {"alerts": []}, rec, *p)
    c = _forge(env, u, now - 100 if c_at is None else c_at)
    (_trust(env) / "revoked.crl").write_bytes(c[0])
    (_trust(env) / "revoked-identities.json").write_bytes(c[1])
    return {"U": u, "P": p, "C": c, "now": now}


def _snapshot(env) -> dict:
    """Every byte the import could change, legacy included (the ledger is judged separately)."""
    out = {}
    roots = [Path(env["reg"].stand), env["world"].root / "trust", env["world"].root / "ca", env["world"].floor_dir]
    for root in roots:
        for p in sorted(root.rglob("*")):
            if p.is_file() and not p.name.endswith(".lock"):
                with open(p, "rb") as fh:                     # not Path.read_bytes: a test may deny that
                    out[str(p)] = hashlib.sha256(fh.read()).hexdigest()
            elif p.is_dir():
                out[str(p)] = "dir"
    return out


def _refused(env, code: str):
    before = _snapshot(env)
    env["ops"].ledger = Ledger()
    with pytest.raises(TP.Refused) as exc:
        TP.import_legacy(env["ops"])
    assert exc.value.code == "trust_publication.legacy_" + code, exc.value
    assert _snapshot(env) == before                          # pointer, generations, journal, legacy, current ...
    assert [e["step"] for e in env["ops"].ledger.events] == ["legacy_" + code]
    return exc.value


# ---------------------------------------------------------------------- the defect, both sides (C6, CC2, CC19)
def test_an_older_legacy_remnant_never_rewinds_the_pointer(env, monkeypatch):
    """The live sequence: rotation (pointer #a) -> rollback writes legacy #a -> rotation with the target unreachable
    (pointer #a+1 = what it holds) -> install. The parent rewound to #a and refused the target; here: `older`,
    nothing but the retirement and one audit event, the target still admitted (C6, C13, C18 / CC2, CC19)."""
    _rotated(env, monkeypatch)
    TP.materialize_legacy(env["reg"].stand)
    old = _number(T.previous_pair(env["reg"].stand))
    _lagging_rotation(env)
    assert _admitted(env) is True
    pointer = TP.pointer_path(env["reg"].stand).read_bytes()
    gens = sorted(p.name for p in TP.generations_dir(env["reg"].stand).iterdir())
    records = _records(env)
    current, used = _pair(env), env["world"].issuer.inventory()["list_number"]
    env["ops"].ledger = Ledger()
    said = TP.import_legacy(env["ops"])
    assert said.startswith("older than the pointer's #%d" % (old + 1)), said
    assert TP.pointer_path(env["reg"].stand).read_bytes() == pointer
    assert sorted(p.name for p in TP.generations_dir(env["reg"].stand).iterdir()) == gens
    assert _records(env) == records and _pair(env) == current
    assert env["world"].issuer.inventory()["list_number"] == used
    assert _admitted(env) is True and not _legacy(env).exists()
    assert [(e["step"], e["number"], e["pointer"]) for e in env["ops"].ledger.events] == [("legacy_older", old, old + 1)]
    assert TP.import_legacy(env["ops"]) == ""                  # a reinstall: nothing there


def test_the_rollback_target_finds_nothing_to_import(env, monkeypatch):
    """C19 / CC16: 60f3a532's `import_legacy` imports a legacy pair whenever BOTH files read and differ from the
    pointer's generation (its code: read both, `!= previous_pair`, `verify_pair`). After the head's install nothing
    readable may be left in `trust-previous/` -- a marker beside the files would not stop that parent."""
    _rotated(env, monkeypatch)
    TP.materialize_legacy(env["reg"].stand)
    env["ops"].refresh_trust(force=True)
    TP.import_legacy(env["ops"])
    d = _legacy(env)
    parent_would_import = False
    try:
        pair = (d / "revoked.crl").read_bytes(), (d / "revoked-identities.json").read_bytes()
        parent_would_import = pair != T.previous_pair(env["reg"].stand)
    except OSError:
        pass
    assert parent_would_import is False and not d.exists()


def test_the_retired_evidence_is_kept_whole(env, monkeypatch):
    """C14 / CC17 / CC18: the retired directory holds both files byte-exact and a durable account of the decision."""
    _rotated(env, monkeypatch)
    TP.materialize_legacy(env["reg"].stand)
    pair = T.previous_pair(env["reg"].stand)
    pointer_before = TP.load_pointer(env["reg"].stand)
    env["ops"].refresh_trust(force=True)
    TP.import_legacy(env["ops"])
    (only,) = _retired(env)
    assert "-older-" in only.name
    assert ((only / "revoked.crl").read_bytes(), (only / "revoked-identities.json").read_bytes()) == pair
    meta = json.loads((only / "retired.json").read_text())
    assert meta["schema"] == TP.RETIRED_SCHEMA and meta["outcome"] == "older" and meta["record_id"] is None
    assert meta["number"] == _number(pair) and meta["revocation_list_sha256"] == hashlib.sha256(pair[0]).hexdigest()
    assert meta["pointer_generation_before"] != pointer_before["generation"]   # the pointer moved on before
    assert meta["pointer_number_before"] == _number(pair) + 1


# ---------------------------------------------------------------------- C2: presence
def test_no_legacy_files_is_nothing(env, monkeypatch):
    """C2: neither file -> "" with no audit event and no change."""
    _rotated(env, monkeypatch)
    before = _snapshot(env)
    env["ops"].ledger = Ledger()
    assert TP.import_legacy(env["ops"]) == ""
    assert _snapshot(env) == before and env["ops"].ledger.events == []


@pytest.mark.parametrize("missing", ["revoked.crl", "revoked-identities.json"])
def test_one_legacy_file_is_incomplete(env, monkeypatch, missing):
    """C2: exactly one file (a torn save) refuses `legacy_incomplete`."""
    _rotated(env, monkeypatch)
    TP.materialize_legacy(env["reg"].stand)
    env["ops"].refresh_trust(force=True)
    (_legacy(env) / missing).unlink()
    _refused(env, "incomplete")


def test_an_unreadable_legacy_file_is_incomplete(env, monkeypatch):
    """C2: a read failure on either file refuses `legacy_incomplete`."""
    _rotated(env, monkeypatch)
    TP.materialize_legacy(env["reg"].stand)
    env["ops"].refresh_trust(force=True)
    real = Path.read_bytes

    def denied(self):
        if self.parent.name == T.PREVIOUS_DIR and self.name == "revoked.crl":
            raise PermissionError("denied")
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", denied)
    _refused(env, "incomplete")


@pytest.mark.parametrize("which", ["crl", "tombstones"])
def test_a_malformed_legacy_document_is_malformed(env, monkeypatch, which):
    """C2: bytes that do not parse refuse `legacy_malformed` (never `incomplete`, never `foreign`)."""
    _rotated(env, monkeypatch)
    crl, tombs = _pair(env)
    T.save_previous_pair(env["reg"].stand, b"not a list" if which == "crl" else crl,
                         b"{not json" if which == "tombstones" else tombs)
    _refused(env, "malformed")


# ---------------------------------------------------------------------- C3: authentication (CC4, CC5, CC10)
def test_a_foreign_signed_list_is_foreign_even_with_an_older_number(env, monkeypatch):
    """C3 / CC4 / CC10: an older-LOOKING list of another CA is refused, never ignored as older."""
    _rotated(env, monkeypatch)
    key, cert, _ = env["world"].foreign_ca()
    _crl, tombs = _pair(env)
    T.save_previous_pair(env["reg"].stand, rl.build(key, cert, {}, number=1, now=time.time()), tombs)
    _refused(env, "foreign")


def test_foreign_signed_tombstones_are_foreign(env, monkeypatch):
    """C3 / CC4."""
    _rotated(env, monkeypatch)
    key, _cert, _ = env["world"].foreign_ca()
    crl, _t = _forge(env, 1, time.time())
    _c, tombs = _forge(env, 1, time.time(), tomb_key=key)
    T.save_previous_pair(env["reg"].stand, crl, tombs)
    _refused(env, "foreign")


def test_tombstones_of_another_deployment_are_foreign(env, monkeypatch):
    """C3 / CC5: correctly signed, other deployment."""
    _rotated(env, monkeypatch)
    T.save_previous_pair(env["reg"].stand, *_forge(env, 1, time.time(), deployment="elsewhere"))
    _refused(env, "foreign")


def test_a_list_naming_another_issuer_is_foreign(env, monkeypatch):
    """C3 / CC5: signed with the deployment's key, but naming another issuer."""
    _rotated(env, monkeypatch)
    k, _c = _ca(env)
    _key, cert, _ = env["world"].foreign_ca()
    _crl, tombs = _forge(env, 1, time.time())
    T.save_previous_pair(env["reg"].stand, rl.build(k, cert, {}, number=1, now=time.time()), tombs)
    _refused(env, "foreign")


# ---------------------------------------------------------------------- C4: the pointer (CC3)
@pytest.mark.parametrize("damage", ["json", "dangling", "traversal", "digest"])
def test_a_damaged_pointer_is_refused_and_never_repaired_from_legacy(env, monkeypatch, damage):
    """C4 / CC3: a present pointer that names no consistent generation refuses `legacy_pointer_unreadable`; the
    legacy pair (here a genuine newer one) is never read in its place."""
    _rotated(env, monkeypatch)
    T.save_previous_pair(env["reg"].stand, *_pair(env))
    p = TP.pointer_path(env["reg"].stand)
    doc = json.loads(p.read_text())
    p.write_bytes({"json": b"{not json",
                   "dangling": json.dumps(dict(doc, generation="g-999999999999")).encode(),
                   "traversal": json.dumps(dict(doc, generation="../previous")).encode(),
                   "digest": json.dumps(dict(doc, tombstones_sha256="0" * 64)).encode()}[damage])
    _refused(env, "pointer_unreadable")
    assert T.previous_pair(env["reg"].stand) is None


def test_an_unverifiable_current_pair_is_refused(env, monkeypatch):
    """R2: no ordering against a current pair this control does not believe."""
    _rotated(env, monkeypatch)
    T.save_previous_pair(env["reg"].stand, *_pair(env))
    env["ops"].effective_time = lambda: time.time() + 30 * 86400   # the current pair expired by then
    _refused(env, "current_unverifiable")


# ---------------------------------------------------------------------- C5: equality (CC6)
def test_the_pointers_own_bytes_are_equal(env, monkeypatch):
    """C5: the rollback's own materialisation, read back before any rotation: a no-op that retires once."""
    _rotated(env, monkeypatch)
    TP.materialize_legacy(env["reg"].stand)
    pointer = TP.pointer_path(env["reg"].stand).read_bytes()
    records = _records(env)
    said = TP.import_legacy(env["ops"])
    assert said.startswith("equal to the pointer's generation"), said
    assert TP.pointer_path(env["reg"].stand).read_bytes() == pointer and _records(env) == records
    assert [p.name.split("-")[1] for p in _retired(env)] == ["equal"]
    assert TP.import_legacy(env["ops"]) == ""


def test_a_re_encoding_of_the_same_statements_is_equal(env, monkeypatch):
    """C5 / CC6: other PEM line endings and other JSON layout, the same signed statements -> `equal`."""
    _rotated(env, monkeypatch)
    crl, tombs = T.previous_pair(env["reg"].stand)
    doc = json.loads(tombs)
    T.save_previous_pair(env["reg"].stand, crl.replace(b"\n", b"\r\n"),
                         json.dumps(doc, sort_keys=False, indent=3).encode("utf-8"))
    assert TP.import_legacy(env["ops"]).startswith("equal to the pointer's generation")


# ---------------------------------------------------------------------- C6/C7/C8/C9: the order (CC7, CC8, CC9, CC13)
def test_an_expired_older_pair_is_still_older(env, monkeypatch):
    """C6 / CC9: an authenticated older pair is ignored and retired whatever its time (live #11 after 2026-10-28)."""
    _rotated(env, monkeypatch)
    T.save_previous_pair(env["reg"].stand, *_forge(env, 1, time.time() - 30 * 86400))
    assert TP.import_legacy(env["ops"]).startswith("older than the pointer's")


def test_a_future_dated_older_pair_is_still_older(env, monkeypatch):
    """C6: older and dated beyond the skew: still `older` (its age cannot matter once it is not imported)."""
    _rotated(env, monkeypatch)
    T.save_previous_pair(env["reg"].stand, *_forge(env, 1, time.time() + 86400))
    assert TP.import_legacy(env["ops"]).startswith("older than the pointer's")


def _like(env, number, pair, **kw):
    """A pair numbered `number`, signed with exactly the list and tombstone times of `pair`."""
    return _forge(env, number, TP._this_update(pair[0]), tomb_at=TI.facts_of(*pair)["tombstones_published_at"], **kw)


# Each of the next three is dated so that EVERY later check passes: only its own guard refuses it.
def test_the_pointers_number_with_other_statements_is_equivocation(env, monkeypatch):
    """C7 / CC7: a different list with the pointer's number (dated like the pointer's, one more serial)."""
    _rotated(env, monkeypatch)
    T.save_previous_pair(env["reg"].stand, *_like(env, _pointer_number(env), T.previous_pair(env["reg"].stand),
                                                       revoked={"7a": time.time() - 60}))
    _refused(env, "equivocation")


def test_the_current_number_with_other_bytes_is_equivocation(env, monkeypatch):
    """C7 / CC13: a different list with the current pair's number -- even with equal content and time."""
    _rotated(env, monkeypatch)
    T.save_previous_pair(env["reg"].stand, *_like(env, _number(_pair(env)), _pair(env)))
    _refused(env, "equivocation")


def test_a_number_beyond_the_current_is_ahead(env, monkeypatch):
    """C8 / CC8: #C+1 = #U+1, signed by the deployment's own CA, is refused `legacy_ahead`."""
    _rotated(env, monkeypatch)
    T.save_previous_pair(env["reg"].stand, *_like(env, _number(_pair(env)) + 1, _pair(env)))
    _refused(env, "ahead")


def test_a_current_pair_beyond_the_issuers_numbers_is_ahead(env, monkeypatch):
    """C8: a deployment pair whose number the issuer never used is no ground for an order."""
    s = _scene(env)
    T.save_previous_pair(env["reg"].stand, *_forge(env, s["U"] - 1, s["now"] - 2000))
    real = env["world"].issuer.next_publication
    monkeypatch.setattr(env["world"].issuer, "next_publication", lambda: dict(real(), number=s["U"]))
    _refused(env, "ahead")


def test_a_newer_legacy_between_pointer_and_current_is_imported_once(env, monkeypatch):
    """C9 / C12: the genuine cc3e1646 window -- the parent saved #P+1 and published #P+2 -- is imported: ONE
    record, ONE generation, ONE pointer replacement, then retired; a second install does nothing."""
    _rotated(env, monkeypatch)
    middle = _pair(env)                                       # #P+1: what a parent refresh saves before publishing
    env["world"].issuer.publish()                             # ... and its publication: #P+2, no predecessor saved
    T.save_previous_pair(env["reg"].stand, *middle)
    records, gens = len(_records(env)), len(list(TP.generations_dir(env["reg"].stand).iterdir()))
    env["ops"].ledger = Ledger()
    said = TP.import_legacy(env["ops"])
    assert said.startswith("imported g-"), said
    assert T.previous_pair(env["reg"].stand) == middle and _number(middle) == _number(_pair(env)) - 1
    new = _records(env)[records:]
    assert [(r["reason"], r["state"], r["previous"]["number"]) for r in new] == [("import_legacy", "done", _number(middle))]
    assert len(list(TP.generations_dir(env["reg"].stand).iterdir())) == gens + 1
    assert [e["step"] for e in env["ops"].ledger.events] == ["begun", "previous_saved", "terminal", "legacy_retired"]
    assert [p.name.split("-")[1] for p in _retired(env)] == ["imported"]
    assert json.loads((_retired(env)[0] / "retired.json").read_text())["record_id"] == new[0]["record_id"]
    assert TP.import_legacy(env["ops"]) == "" and len(_records(env)) == records + 1


def test_without_a_pointer_the_first_migration_still_imports(env):
    """C9: a stand no publication authority has written yet (r-1 live): an authenticated legacy pair <= current."""
    T.save_previous_pair(env["reg"].stand, *_pair(env))
    assert TP.load_pointer(env["reg"].stand) is None
    assert TP.import_legacy(env["ops"]).startswith("imported g-")
    assert T.previous_pair(env["reg"].stand) == _pair(env)


def test_a_newer_predecessor_with_other_content_is_imported_and_admits_nothing(env, monkeypatch):
    """C13 (Codex D2): after a security publication the newer predecessor's content differs: imported (it IS the
    predecessor), yet `trust_current` never admits a target holding it."""
    _rotated(env, monkeypatch)
    middle = _pair(env)
    tid = _provision(env)
    env["ops"].probe(tid)
    inst = env["reg"].load().target(tid).worker["instance"]
    env["world"].issuer.recover_entry("worker", inst, publish=True)   # a parent-side security publication
    assert TI.facts_of(*middle)["content_sha256"] != TI.facts_of(*_pair(env))["content_sha256"]
    T.save_previous_pair(env["reg"].stand, *middle)
    assert TP.import_legacy(env["ops"]).startswith("imported g-")
    env["acks"].record("managed-w1", dict(TI.facts_of(*middle), acked_at=time.time()))
    assert _admitted(env) is False


# ---------------------------------------------------------------------- C10/C11: time (CC11, CC12)
def _candidate(env, s, *, at=None, tomb_at=None):
    at = s["now"] - 2000 if at is None else at
    pair = _forge(env, s["U"] - 1, at, tomb_at=tomb_at)
    T.save_previous_pair(env["reg"].stand, *pair)
    return pair


def test_a_candidate_in_the_scene_is_imported(env):
    """The controlled scene's control: #U-1 between #U-2 and #U in number and time imports."""
    s = _scene(env)
    pair = _candidate(env, s)
    assert TP.import_legacy(env["ops"]).startswith("imported g-")
    assert T.previous_pair(env["reg"].stand) == pair


@pytest.mark.parametrize("delta,ok", [(300, True), (301, False)])
def test_the_future_bound_is_exact(env, delta, ok):
    """C10 / CC11: a candidate dated exactly effective + 300 s passes; one second more refuses."""
    s = _scene(env)
    _candidate(env, s)
    at = s["now"] - 2000
    env["ops"].effective_time = lambda: at - delta         # the candidate is `delta` s ahead of this side's time
    if ok:
        assert TP.import_legacy(env["ops"]).startswith("imported g-")
    else:
        _refused(env, "not_yet_valid")


def test_a_current_pair_of_another_ca_is_refused(env, monkeypatch):
    """R2 / DECISION-0003: no order against a current pair this control's anchor does not authenticate."""
    _rotated(env, monkeypatch)
    TP.materialize_legacy(env["reg"].stand)
    key, cert, _ = env["world"].foreign_ca()
    (_trust(env) / "revoked.crl").write_bytes(rl.build(key, cert, {}, number=99, now=time.time()))
    _refused(env, "current_unverifiable")


@pytest.mark.parametrize("which", ["crl", "tombstones"])
def test_a_candidate_expiring_at_the_effective_time_is_expired(env, which):
    """C10: next_update / not_after <= effective time refuses `legacy_expired`."""
    s = _scene(env)
    at = s["now"] - 2000
    _candidate(env, s, at=at)
    if which == "tombstones":                                 # the tombstones end first: list valid longer
        pair = _forge(env, s["U"] - 1, at, tomb_at=at - 100)
        T.save_previous_pair(env["reg"].stand, *pair)
        end = at - 100 + tomb.VALID_SECONDS
    else:
        end = at + rl.VALID_SECONDS
    env["ops"].effective_time = lambda: end                  # the current pair (signed 1900 s later) still valid
    _refused(env, "expired")


@pytest.mark.parametrize("offset,ok", [(-300, True), (-301, False), (600, True), (601, False)])
def test_the_torn_pair_bound_is_exact(env, offset, ok):
    """C11 / CC12: the tombstones within [thisUpdate - 300 s, thisUpdate + 600 s] of the list; one second outside
    refuses `legacy_inconsistent`."""
    s = _scene(env)
    at = s["now"] - 2000
    _candidate(env, s, at=at, tomb_at=at + offset)
    if ok:
        assert TP.import_legacy(env["ops"]).startswith("imported g-")
    else:
        _refused(env, "inconsistent")


@pytest.mark.parametrize("when", ["before_pointer", "after_current"])
def test_a_candidate_outside_the_pointer_current_interval_is_inconsistent(env, when):
    """C11: thisUpdate / published_at must lie between the pointer's and the current pair's."""
    s = _scene(env)
    at = s["now"] - 5000 if when == "before_pointer" else s["now"] - 50
    _candidate(env, s, at=at)
    _refused(env, "inconsistent")


def test_clock_jumps_never_change_the_order(env, monkeypatch):
    """Negative: effective time -10 / +10 days: an authenticated older pair is still older (retired); only a
    candidate is judged by time."""
    _rotated(env, monkeypatch)
    for jump in (-10 * 86400, 10 * 86400):
        T.save_previous_pair(env["reg"].stand, *_forge(env, 1, time.time() - 3600))
        env["ops"].effective_time = lambda: time.time() + jump
        assert TP.import_legacy(env["ops"]).startswith("older than the pointer's")
    env["ops"].effective_time = time.time


# ---------------------------------------------------------------------- C15/C16: crashes and open records (CC14, CC15)
def _newer_window(env, monkeypatch):
    _rotated(env, monkeypatch)
    middle = _pair(env)
    env["world"].issuer.publish()
    T.save_previous_pair(env["reg"].stand, *middle)
    return middle


@pytest.mark.parametrize("seam", ["tp:begun", "generation", "pointer", "tp:previous_saved", "tp:terminal",
                                  "tp:legacy_retired_meta", "tp:legacy_retired"])
def test_a_crash_anywhere_in_an_import_resumes_without_rewind(env, monkeypatch, seam):
    """C15 / negative (SIGKILL at every seam): the next install resumes; exactly one terminal per record, the
    pointer ends on the newer pair, never below what it was, and the legacy directory ends retired."""
    middle = _newer_window(env, monkeypatch)
    before = _pointer_number(env)
    real_write = TP._write_settled

    def write(path, data):
        if seam == "generation" and path.name == TP.T.PREVIOUS_POINTER:
            raise Crash(seam)                                 # the generation is written, the pointer is not
        real_write(path, data)
        if seam == "pointer" and path.name == TP.T.PREVIOUS_POINTER:
            raise Crash(seam)                                 # the pointer is replaced, the record still `begun`
    monkeypatch.setattr(TP, "_write_settled", write)

    def crash(step):
        if step == seam:
            raise Crash(step)
    env["ops"]._crash_after = crash
    with pytest.raises(Crash):
        TP.import_legacy(env["ops"])
    env["ops"]._crash_after = lambda step: None
    monkeypatch.setattr(TP, "_write_settled", real_write)
    assert (_pointer_number(env) or 0) >= before
    TP.import_legacy(env["ops"])
    assert TP.import_legacy(env["ops"]) == ""
    assert T.previous_pair(env["reg"].stand) == middle and not _legacy(env).exists()
    imports = [r["state"] for r in _records(env) if r["reason"] == "import_legacy"]
    # before the pointer selected its generation: closed `superseded`, the next install imports again (one done);
    # from then on: the one record is resumed `done` and the next install finds `equal` and retires
    assert imports == ([TP.SUPERSEDED, TP.DONE] if seam in ("tp:begun", "generation") else [TP.DONE]), imports
    for d in _retired(env):
        assert (d / "retired.json").is_file()                 # CC18: never an unaccounted retired directory


def test_a_crashed_import_is_never_resumed_as_a_publication(env, monkeypatch):
    """C15 / CC14: the cycle's local phase meets the open import record: closed `done`, nothing published."""
    _newer_window(env, monkeypatch)

    def crash(step):
        if step == "tp:previous_saved":
            raise Crash(step)
    env["ops"]._crash_after = crash
    with pytest.raises(Crash):
        TP.import_legacy(env["ops"])
    env["ops"]._crash_after = lambda step: None
    used, current = env["world"].issuer.inventory()["list_number"], _pair(env)
    with TP._publication_lock(env["ops"]):
        TP.local(env["ops"], TP.new_report("floor"), rotate=False)
    rec = [r for r in _records(env) if r["reason"] == "import_legacy"][-1]
    assert rec["state"] == TP.DONE and rec["result"] is None
    assert env["world"].issuer.inventory()["list_number"] == used and _pair(env) == current


def test_a_crashed_import_whose_generation_is_not_selected_is_superseded(env, monkeypatch):
    """C15 / CC15: `previous_saved`, but the pointer does not select its generation -> `superseded`, the pointer
    left as it is."""
    _newer_window(env, monkeypatch)
    old = TP.pointer_path(env["reg"].stand).read_bytes()

    def crash(step):
        if step == "tp:previous_saved":
            raise Crash(step)
    env["ops"]._crash_after = crash
    with pytest.raises(Crash):
        TP.import_legacy(env["ops"])
    env["ops"]._crash_after = lambda step: None
    TP.pointer_path(env["reg"].stand).write_bytes(old)        # the pointer replacement did not survive
    with TP._publication_lock(env["ops"]):
        TP.local(env["ops"], TP.new_report("floor"), rotate=False)
    rec = [r for r in _records(env) if r["reason"] == "import_legacy"][-1]
    assert rec["state"] == TP.SUPERSEDED and TP.pointer_path(env["reg"].stand).read_bytes() == old


@pytest.mark.parametrize("seam", ["tp:begun", "tp:previous_saved"])
def test_an_open_ordinary_publication_refuses_the_import(env, monkeypatch, seam):
    """C16: a crashed local phase of a rotation must be resumed by the cycle, not raced by the install."""
    _rotated(env, monkeypatch)
    TP.materialize_legacy(env["reg"].stand)

    def crash(step):
        if step == seam:
            raise Crash(step)
    env["ops"]._crash_after = crash
    with pytest.raises(Crash):
        env["ops"].refresh_trust(force=True)
    env["ops"]._crash_after = lambda step: None
    _refused(env, "open_record")


def test_a_failed_pointer_replacement_keeps_the_old_pointer_and_the_evidence(env, monkeypatch):
    """Negative: the pointer write fails -> the old pointer stays authoritative, nothing is retired; the next
    install imports (one `done`)."""
    middle = _newer_window(env, monkeypatch)
    old = TP.pointer_path(env["reg"].stand).read_bytes()
    real_write = TP._write_settled

    def write(path, data):
        if path.name == TP.T.PREVIOUS_POINTER:
            raise OSError("disk full")
        real_write(path, data)
    monkeypatch.setattr(TP, "_write_settled", write)
    with pytest.raises(OSError):
        TP.import_legacy(env["ops"])
    monkeypatch.setattr(TP, "_write_settled", real_write)
    assert TP.pointer_path(env["reg"].stand).read_bytes() == old and _legacy(env).is_dir() and _retired(env) == []
    assert TP.import_legacy(env["ops"]).startswith("imported g-")
    assert T.previous_pair(env["reg"].stand) == middle
    assert [r["state"] for r in _records(env) if r["reason"] == "import_legacy"] == [TP.SUPERSEDED, TP.DONE]


def test_a_failed_retirement_is_named_and_the_next_install_retires(env, monkeypatch):
    """DECISION-0002 / negative: the rename fails -> `legacy_retire_failed`, `trust-previous/` intact; the next
    install decides again (`older`) and retires."""
    _rotated(env, monkeypatch)
    TP.materialize_legacy(env["reg"].stand)
    env["ops"].refresh_trust(force=True)
    pointer = TP.pointer_path(env["reg"].stand).read_bytes()
    real = os.replace

    def no_rename(src, dst):
        if Path(src).name == T.PREVIOUS_DIR:
            raise OSError("cross-device")
        return real(src, dst)
    monkeypatch.setattr(TP.os, "replace", no_rename)
    with pytest.raises(TP.Refused) as exc:
        TP.import_legacy(env["ops"])
    assert exc.value.code == "trust_publication.legacy_retire_failed" and _legacy(env).is_dir()
    monkeypatch.setattr(TP.os, "replace", real)
    assert TP.import_legacy(env["ops"]).startswith("older than the pointer's")
    assert TP.pointer_path(env["reg"].stand).read_bytes() == pointer and not _legacy(env).exists()


# ---------------------------------------------------------------------- C18: admission during the install
def test_targets_on_current_and_on_the_predecessor_stay_admitted(env, monkeypatch):
    """C18: two targets, one exactly on C, one exactly on the admissible predecessor P; the install with an older
    remnant changes neither admission."""
    _rotated(env, monkeypatch)
    tid = _provision(env)
    env["ops"].probe(tid)
    assert env["ops"].refresh_trust()["lagging"] == []
    TP.materialize_legacy(env["reg"].stand)
    env["ops"].refresh_trust(force=True)                      # both exactly on C
    _lagging_rotation(env, "managed-w1")                      # managed-w1 on P, the customer target on C
    assert _admitted(env, "managed-w1") and _admitted(env, tid)
    assert TI.differences(env["acks"].load()[tid], TI.facts_of(*_pair(env))) == []
    TP.import_legacy(env["ops"])
    assert _admitted(env, "managed-w1") and _admitted(env, tid)


# ---------------------------------------------------------------------- C20: audit
def test_an_audit_failure_is_named_and_undoes_nothing(env, monkeypatch):
    """C20: the ledger refuses the append -> named in the answer; the retirement stands."""
    _rotated(env, monkeypatch)
    TP.materialize_legacy(env["reg"].stand)
    env["ops"].refresh_trust(force=True)
    env["ops"].ledger = Ledger(fail=True)
    said = TP.import_legacy(env["ops"])
    assert said.startswith("older than") and "audit_failed legacy legacy_older (OSError)" in said
    assert not _legacy(env).exists()


# ---------------------------------------------------------------------- C1/C5/C7: real multiprocess races (CC1)
def _spec(env, **extra) -> dict:
    return dict({"stand": str(env["reg"].stand), "world": str(env["world"].root),
                 "op": env["reg"]._private.private_bytes_raw().hex(), "keyring": str(env["keyring"]),
                 "deployment": env["world"].deployment, "bundles": str(env["tmp"] / "bundles"),
                 "floor": str(env["world"].floor_dir)}, **extra)


def _child(spec: dict, action: str, ready, done) -> None:
    """Another process with its own Operations on the same stand: takes the publication lock, says so, holds it
    a moment, then rotates / publishes a revocation (or runs the floor run, which takes the lock itself)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from agentnode_sdk.pki.issuer import Issuer
    from agentnode_sdk.remote_mcp.target_ops import Operations
    from tests.remote_mcp.target_fixtures import raw_pub
    op = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(spec["op"]))
    reg = T.Register(spec["stand"], raw_pub(op), operator_private=op)
    acks = T.TrustAcks(spec["stand"], raw_pub(op), operator_private=op)
    issuer = Issuer(Path(spec["world"]) / "ca", Path(spec["world"]) / "trust")
    ops = Operations(stand=spec["stand"], register=reg, acks=acks, issuer=issuer, keyring_path=spec["keyring"],
                     gateway_instance="g1", deployment=spec["deployment"], bundles_dir=spec["bundles"],
                     worker_for=lambda t: (_ for _ in ()).throw(OSError("no network here")), slot=None)
    TP.ROTATE_AFTER_SECONDS = 0.0
    try:
        if action == "floor":
            ready.set()
            for _ in range(3):
                control_floor.run(ops, spec["floor"])
            return
        with TP._publication_lock(ops):
            ready.set()
            time.sleep(1.5)
            if action == "rotate":
                TP.local(ops, TP.new_report("race"), force=True)
            elif action == "revoke":
                issuer.recover_entry("worker", spec["instance"], publish=False)
                TP.local(ops, TP.new_report("race"), rotate=False)
    finally:
        done.set()


def _race(env, action: str, **extra):
    ctx = multiprocessing.get_context("spawn")
    ready, done = ctx.Event(), ctx.Event()
    p = ctx.Process(target=_child, args=(_spec(env, **extra), action, ready, done))
    p.start()
    try:
        assert ready.wait(60), "the child never took the lock"
        said = TP.import_legacy(env["ops"])
    finally:
        p.join(60)
    assert p.exitcode == 0, p.exitcode
    return said


def test_an_install_racing_a_rotation_decides_after_it(env, monkeypatch):
    """C1 / C5 / CC1: legacy = the current pair (a candidate BEFORE the child's rotation); another process holds the
    lock and rotates (the pointer becomes that pair). Decided under the lock, the install sees `equal` and writes
    no record; a decision taken before the lock would import a record over the rotation."""
    _rotated(env, monkeypatch)
    T.save_previous_pair(env["reg"].stand, *_pair(env))
    before = _pointer_number(env)
    said = _race(env, "rotate")
    assert said.startswith("equal to the pointer's generation"), said
    assert _pointer_number(env) == before + 1
    assert [r for r in _records(env) if r["reason"] == "import_legacy"] == []


def test_an_install_racing_a_revocation_is_serialised(env, monkeypatch):
    """C5: a security publication in another process under the lock: the install decides on what it left."""
    _rotated(env, monkeypatch)
    tid = _provision(env)
    env["ops"].probe(tid)
    TP.materialize_legacy(env["reg"].stand)
    env["ops"].refresh_trust(force=True)
    before = _pointer_number(env)
    said = _race(env, "revoke", instance=env["reg"].load().target(tid).worker["instance"])
    assert said.startswith("older than the pointer's"), said
    assert _pointer_number(env) >= before
    assert all(r["state"] in TP.TERMINAL or r["phase"] in ("published", "distributing") for r in _records(env))


def test_an_install_racing_the_floor_run_is_serialised(env, monkeypatch):
    """C5: the floor run (its own lock wait) in another process while the install decides."""
    _rotated(env, monkeypatch)
    TP.materialize_legacy(env["reg"].stand)
    env["ops"].refresh_trust(force=True)
    pointer = TP.pointer_path(env["reg"].stand).read_bytes()
    said = _race(env, "floor")
    assert said.startswith("older than the pointer's"), said
    assert TP.pointer_path(env["reg"].stand).read_bytes() == pointer
