"""The ONE publication authority for the remote-MCP control's trust pair (trust-floor, Codex Q-TFL-0001).

Until cc3e1646 two writers published the deployment's revocation list and tombstones independently: the
control floor's root run (`Issuer.tick`, every 5 min) re-signed them once they were 24 h old -- without
saving the pair the targets held and without handing anything to them -- and the trust refresh
(00/06/12/18 UTC) saved a predecessor, published and pushed. A target holding the pair the tick replaced
was neither current nor the saved predecessor, so the router refused it until the next refresh: a daily
window in which every run on the managed default was refused.

Now every publication on the remote-MCP control -- the age rotation, a forced one, a revocation's, the
reconciliation of a list that lacks a revoked serial or a withdrawn identity -- is one journaled record
of this module, under the publication lock `stand/targets/.trust-refresh.lock`:

    begin       the record, fsynced, naming the reason, the CURRENT pair's exact facts (`from`) and
                what this record's publication will be (`expect`: the inventory's next list number,
                used once, and the content digest it will carry) -- BEFORE any side effect
    previous    the current pair's bytes, verified under the control's anchor, written as an immutable
                generation directory and selected by replacing ONE pointer file (a reader sees the old
                complete generation or the new one, never a mixed pair)
    published   `Issuer.publish()`; the record then holds the resulting pair's exact facts
    distribute  the ONE pair read once is pushed to every live target WITHOUT the publication lock
                (a network wait never delays a revocation) but under `.trust-distribute.lock`; an
                acknowledgement is recorded only when it is exactly that pair AND that pair is still
                the deployment's (else it is a historical attempt, `trust.pair_changed`)
    terminal    done | done_degraded | superseded, exactly once (compare-and-set under the lock);
                every later retry is its own `distribution` record linked to the publication

A crash at any boundary leaves an open record; the next cycle from any entry point (the 5-minute cycle
timer, the floor service's local reconciliation, the 6-hourly refresh, an operator, a revocation)
completes the SAME record: "published by this record" means the deployment's number equals the
journaled `expect` number and its content digest the journaled one -- never just "a higher number".

A content-equal ROTATION starts only when every live target exactly acknowledges the current pair, so the
saved predecessor is what every acknowledged target holds and `targets.trust_current` keeps admitting it
until its new acknowledgement (owner point 1); the guard yields only when either document has less than
72 h left. A SECURITY publication (new content) never waits for the guard, a push or a lock held across
the network: old acknowledgements are inadmissible at once (the content differs) and distribution starts
immediately and is retried every cycle.

An exact target is pushed only at a rotation, so its acknowledgement no longer says it is still there.
Every cycle that did not fail therefore also REACHES every live target (trust-watchdog D3): an exact push
of this run counts; every other live target gets the router's own handshake-only preflight
(`TargetRouter.preflight`, nothing sent, nothing changed), bounded in threads and time. The result is
recorded in the cycle state for the watchdog -- it never changes this cycle's status or exit code.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from concurrent.futures import ThreadPoolExecutor, wait as _wait
from pathlib import Path
from typing import Any, Callable

from agentnode_sdk.pki import revocation as _revocation
from agentnode_sdk.pki import tombstones as _tombstones
from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.remote_mcp.catalog import _fsync_dir, _write_durably
from agentnode_sdk.worker import trust_install as _trust_install

SCHEMA = "agentnode.trust-publication.v1"
POINTER_SCHEMA = "agentnode.trust-previous-pointer.v1"
CYCLE_STATE_SCHEMA = "agentnode.trust-cycle-state.v1"
RECORDS = "records"
DISTRIBUTION_LOCK_NAME = ".trust-distribute.lock"
#: How long a manual refresh, the 6-hourly timer or a revocation waits for a distribution in progress.
DISTRIBUTION_WAIT_SECONDS = 600.0
#: A content-equal rotation is due at this age of the current list; None = the issuer's own refresh age
#: (`pki.revocation.REFRESH_AFTER_SECONDS`, read at each decision: ONE threshold for both).
ROTATE_AFTER_SECONDS: float | None = None


def _rotate_after() -> float:
    return float(_revocation.REFRESH_AFTER_SECONDS if ROTATE_AFTER_SECONDS is None else ROTATE_AFTER_SECONDS)
#: Below this much validity left on either document the rotation guard no longer waits for lagging targets.
GUARD_RESERVE_SECONDS = 72 * 3600
#: The watchdog's bounds (Codex D9): an open publication record, and a target not exactly on the current pair.
HUNG_AFTER_SECONDS = 600
DISTRIBUTION_WINDOW_SECONDS = 600
OPEN, DONE, DONE_DEGRADED, SUPERSEDED = "open", "done", "done_degraded", "superseded"
TERMINAL = (DONE, DONE_DEGRADED, SUPERSEDED)
PUBLICATION, DISTRIBUTION = "publication", "distribution"
#: trust-watchdog D3/R2: each preflight's budget, how many run at once, and the deadline for all of them
#: (well below the cycle service's 240 s TimeoutStartSec, whatever the number of live targets).
REACH_BUDGET_SECONDS = 10.0
REACH_THREADS = 8
REACH_DEADLINE_SECONDS = 60.0
#: The monotonic clock the cycle state's same-boot stamps come from (trust-watchdog D5); tests replace it.
monotonic: Callable[[], float] = time.monotonic
BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"


def current_boot_id() -> str | None:
    """This boot's id (the kernel's), or None where there is none: a monotonic stamp is comparable only within it."""
    try:
        return Path(BOOT_ID_PATH).read_text(encoding="ascii").strip() or None
    except OSError:
        return None


class Refused(Exception):
    """A named failure of the publication authority (never a success, never a silent skip)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code + (": " + detail if detail else ""))
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------- layout
def publication_dir(stand) -> Path:
    return Path(stand) / "targets" / T.PUBLICATION_DIR


def records_dir(stand) -> Path:
    return publication_dir(stand) / RECORDS


def generations_dir(stand) -> Path:
    return publication_dir(stand) / T.PREVIOUS_GENERATIONS


def pointer_path(stand) -> Path:
    return publication_dir(stand) / T.PREVIOUS_POINTER


def cycle_state_path(stand) -> Path:
    """`<stand>/../alerts/trust-cycle.json` -- beside the watchdog's and the renewal's state."""
    return Path(stand).resolve().parent / "alerts" / "trust-cycle.json"


def _short(facts: dict | None) -> dict | None:
    if not facts:
        return None
    return {"number": facts.get("revocation_list_number"),
            "crl": str(facts.get("revocation_list_sha256", ""))[:16],
            "tombstones": str(facts.get("tombstones_sha256", ""))[:16],
            "content": str(facts.get("content_sha256", ""))[:16]}


def _same_pair(a: dict | None, b: dict | None) -> bool:
    return bool(a) and bool(b) and not _trust_install.differences(a, b)


# ---------------------------------------------------------------------- the journal
def _read_settled(path: Path) -> bytes:
    """Read a file another process may be replacing at this moment: a rename is atomic on Linux, but Windows
    refuses the open for an instant (sharing violation) -- retried briefly, never answered with a guess."""
    import time
    for attempt in range(40):
        try:
            return path.read_bytes()
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.05)
    raise AssertionError("unreachable")


def load_records(stand) -> list[dict]:
    """Every record, oldest first. Unreadable, foreign or contradictory raises ValueError (named, red)."""
    d = records_dir(stand)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("r-*.json")):
        try:
            doc = json.loads(_read_settled(p).decode("utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("trust publication record unreadable: %s" % p.name) from exc
        if (not isinstance(doc, dict) or doc.get("schema") != SCHEMA or doc.get("record_id") != p.stem
                or doc.get("kind") not in (PUBLICATION, DISTRIBUTION) or doc.get("state") not in (OPEN,) + TERMINAL):
            raise ValueError("trust publication record malformed: %s" % p.name)
        out.append(doc)
    return out


def _next_id(stand) -> str:
    d = records_dir(stand)
    seqs = [int(p.stem[2:]) for p in d.glob("r-*.json") if p.stem[2:].isdigit()] if d.is_dir() else []
    return "r-%012d" % (max(seqs, default=0) + 1)


def _write_settled(path: Path, data: bytes) -> None:
    """`_write_durably`, retried for an instant when Windows refuses the rename over a file a reader holds
    open (POSIX renames over it atomically; the control is Linux)."""
    import time
    for attempt in range(40):
        try:
            _write_durably(path, data)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.05)


def _write_record(stand, rec: dict) -> None:
    _write_settled(records_dir(stand) / (rec["record_id"] + ".json"),
                   (json.dumps(rec, indent=1, sort_keys=True) + "\n").encode("utf-8"))


def load_pointer(stand) -> dict | None:
    """The predecessor pointer, or None when there is none. Unreadable raises ValueError."""
    p = pointer_path(stand)
    if not os.path.lexists(p):
        return None
    try:
        doc = json.loads(p.read_bytes().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("predecessor pointer unreadable") from exc
    if not isinstance(doc, dict) or doc.get("schema") != POINTER_SCHEMA or not doc.get("generation"):
        raise ValueError("predecessor pointer malformed")
    return doc


def load_pointer_safely(stand) -> dict | None:
    """The pointer, None when there is none, `{"unreadable": ...}` when there is one that cannot be read."""
    try:
        return load_pointer(stand)
    except ValueError as exc:
        return {"unreadable": str(exc)}


def load_cycle_state(stand) -> dict | None:
    try:
        doc = json.loads(cycle_state_path(stand).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ValueError("trust cycle state unreadable: %s" % type(exc).__name__) from exc
    if not isinstance(doc, dict) or doc.get("schema") != CYCLE_STATE_SCHEMA:
        raise ValueError("trust cycle state malformed")
    return doc


# ---------------------------------------------------------------------- the pair
def verify_pair(ops, crl: bytes, tombs: bytes) -> dict:
    """The exact facts of a pair this control's own anchor verifies at its effective time; ValueError otherwise."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    try:
        anchor = x509.load_pem_x509_certificate((Path(ops.issuer.trust_dir) / "ca.pem").read_bytes())
        effective = float(ops.effective_time())

        def verify(body: bytes, signature: bytes) -> None:
            anchor.public_key().verify(signature, body, ec.ECDSA(hashes.SHA256()))
        _revocation.read(crl, anchor, effective)
        _tombstones.read(tombs, deployment=ops.deployment, verifier=verify).usable_at(effective)
        return _trust_install.facts_of(crl, tombs)
    except Exception as exc:                                  # noqa: BLE001 - not believed
        raise ValueError("the pair does not verify under this control's anchor (%s)" % type(exc).__name__) from exc


def _this_update(crl: bytes) -> float | None:
    try:
        from cryptography import x509
        return _revocation._stamp(x509.load_pem_x509_crl(crl), "last_update")
    except Exception:                                         # noqa: BLE001
        return None


# ---------------------------------------------------------------------- audit
def _audit(ops, report: dict, rec: dict, step: str, **extra) -> None:
    """One `trust_publication` ledger event per transition: ids, states, numbers and short digests only. A
    failed append is named and makes the run non-green; it never undoes what is already in effect."""
    if ops.ledger is None:
        return
    try:
        ops.ledger.append("trust_publication", record_id=rec["record_id"], record_kind=rec["kind"],
                          step=step, state=rec["state"], reason=rec.get("reason", ""),
                          number=(rec.get("result") or rec.get("from") or {}).get("revocation_list_number"),
                          **extra)
    except Exception as exc:                                  # noqa: BLE001 - named, red
        report["alerts"].append("audit_failed %s %s (%s)" % (rec["record_id"], step, type(exc).__name__))


def _transition(ops, report: dict, rec: dict, step: str, **extra) -> None:
    """Journal first (durable), then audit, then the crash seam."""
    _write_record(ops.stand, rec)
    _audit(ops, report, rec, step, **extra)
    ops._crash_after("tp:" + step)


def _close(ops, report: dict, rec: dict, state: str, why: str = "") -> bool:
    """Compare-and-set to a terminal state: only an OPEN record on disk is closed, exactly once."""
    path = records_dir(ops.stand) / (rec["record_id"] + ".json")
    on_disk = json.loads(path.read_bytes().decode("utf-8"))
    if on_disk.get("state") != OPEN:
        return False
    rec.update(state=state, closed_at=round(ops.clock(), 3), error=why or rec.get("error", ""))
    _transition(ops, report, rec, "terminal", terminal=state)
    return True


# ---------------------------------------------------------------------- locks
def _publication_lock(ops):
    from agentnode_sdk.gateway.filelock import LockUnavailable, ProcessLock
    from agentnode_sdk.remote_mcp.target_ops import REFRESH_LOCK_NAME, OperationFailed
    lock = ProcessLock(Path(ops.stand) / "targets" / REFRESH_LOCK_NAME, timeout=ops.refresh_lock_timeout)

    class _Held:
        def __enter__(self):
            (Path(ops.stand) / "targets").mkdir(parents=True, exist_ok=True)
            try:
                lock.__enter__()
            except LockUnavailable as exc:
                raise OperationFailed("trust_refresh.busy", str(exc)) from exc
            return self

        def __exit__(self, *exc):
            return lock.__exit__(*exc)
    return _Held()


def _distribution_lock(ops, wait: float):
    from agentnode_sdk.gateway.filelock import LockUnavailable, ProcessLock
    from agentnode_sdk.remote_mcp.target_ops import OperationFailed
    lock = ProcessLock(Path(ops.stand) / "targets" / DISTRIBUTION_LOCK_NAME, timeout=wait)

    class _Held:
        def __enter__(self):
            try:
                lock.__enter__()
            except LockUnavailable as exc:
                raise OperationFailed("trust_distribution.busy", str(exc)) from exc
            return self

        def __exit__(self, *exc):
            return lock.__exit__(*exc)
    return _Held()


# ---------------------------------------------------------------------- the predecessor
def _save_previous(ops, report: dict, rec: dict, crl: bytes, tombs: bytes) -> None:
    """The verified current pair as an immutable generation, then ONE atomic pointer replacement."""
    facts = verify_pair(ops, crl, tombs)
    if not _same_pair(facts, rec["from"]) or facts.get("content_sha256") != rec["from"].get("content_sha256"):
        raise Refused("trust_publication.previous_mismatch", "the pair to save is not the journaled one")
    gen = "g-" + rec["record_id"][2:]
    gdir = generations_dir(ops.stand)
    target = gdir / gen
    if target.exists():                                       # a resumed save: reuse only an identical generation
        same = ((target / _revocation.LIST_NAME).read_bytes() == crl
                and (target / _tombstones.LIST_NAME).read_bytes() == tombs)
        if not same:
            raise Refused("trust_publication.generation_conflict", gen)
    else:
        gdir.mkdir(parents=True, exist_ok=True)
        tmp = gdir / (".%s.tmp-%s" % (gen, secrets.token_hex(4)))
        tmp.mkdir()
        _write_durably(tmp / _tombstones.LIST_NAME, tombs)
        _write_durably(tmp / _revocation.LIST_NAME, crl)
        os.replace(str(tmp), str(target))
        _fsync_dir(gdir)
    pointer = {"schema": POINTER_SCHEMA, "generation": gen, "record_id": rec["record_id"],
               "revocation_list_sha256": hashlib.sha256(crl).hexdigest(),
               "tombstones_sha256": hashlib.sha256(tombs).hexdigest(),
               "revocation_list_number": facts["revocation_list_number"],
               "content_sha256": facts["content_sha256"], "saved_at": round(ops.clock(), 3)}
    _write_settled(pointer_path(ops.stand), (json.dumps(pointer, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    rec["previous"] = {"generation": gen, "number": facts["revocation_list_number"]}


def materialize_legacy(stand) -> dict:
    """For a rollback while quiescent: write the pointer's generation into the parent's two legacy files
    (`targets/trust-previous/`), then read them back. Returns the facts of what is there now."""
    pair = T.previous_pair(stand)
    if pair is None:
        raise Refused("trust_publication.no_predecessor", "no readable predecessor to materialise")
    T.save_previous_pair(stand, pair[0], pair[1])
    d = Path(stand) / "targets" / T.PREVIOUS_DIR
    back = ((d / _revocation.LIST_NAME).read_bytes(), (d / _tombstones.LIST_NAME).read_bytes())
    if back != pair:
        raise Refused("trust_publication.materialize_mismatch")
    return {"revocation_list_sha256": hashlib.sha256(pair[0]).hexdigest(),
            "tombstones_sha256": hashlib.sha256(pair[1]).hexdigest()}


def import_legacy(ops) -> str:
    """Install-time migration (R3): a legacy predecessor that differs from the pointer's generation (a parent
    publication during a rollback window) becomes a new generation and the pointer selects it. "" = nothing."""
    d = Path(ops.stand) / "targets" / T.PREVIOUS_DIR
    try:
        crl, tombs = (d / _revocation.LIST_NAME).read_bytes(), (d / _tombstones.LIST_NAME).read_bytes()
    except OSError:
        return ""
    current = None
    if os.path.lexists(pointer_path(ops.stand)):
        current = T.previous_pair(ops.stand)
        if current == (crl, tombs):
            return ""
    try:
        facts = verify_pair(ops, crl, tombs)
    except ValueError:
        return "legacy predecessor not verifiable: left as it is"
    report: dict = {"alerts": []}
    with _publication_lock(ops):
        rec = {"schema": SCHEMA, "record_id": _next_id(ops.stand), "kind": PUBLICATION, "reason": "import_legacy",
               "security": False, "created_at": round(ops.clock(), 3), "state": OPEN, "phase": "begun",
               "from": facts, "expect": None, "previous": None, "result": None, "targets": {}, "error": ""}
        _transition(ops, report, rec, "begun")
        _save_previous(ops, report, rec, crl, tombs)
        rec["phase"] = "previous_saved"
        _transition(ops, report, rec, "previous_saved")
        _close(ops, report, rec, DONE, "legacy predecessor imported; nothing published")
    return "imported %s" % rec["previous"]["generation"]


# ---------------------------------------------------------------------- the local phase
def _live(ops) -> list:
    from agentnode_sdk.remote_mcp.target_ops import LIVE
    snap = ops.register.load()
    return [t for t in snap.targets.values() if t.effective_status(snap.now) in LIVE]


def _open(stand, kind: str) -> list[dict]:
    return [r for r in load_records(stand) if r["kind"] == kind and r["state"] == OPEN]


def _judge_after_publish(ops, report: dict, rec: dict) -> str:
    """Where the record's publication stands, from the deployment's pair NOW: "not_published", "published"
    (completed if only the CRL was promoted) or "superseded". Ours = the journaled number AND content."""
    ops.issuer.settle()
    _crl, tombs, cur = ops._pair()
    frm, exp = rec["from"] or {}, rec["expect"] or {}
    if cur is None:
        if not frm:                                           # a security repair of an unreadable pair, not yet done
            return "not_published"
        raise Refused("trust_publication.pair_unreadable")
    if frm and cur["revocation_list_sha256"] == frm.get("revocation_list_sha256"):
        return "not_published"
    if int(cur["revocation_list_number"]) != int(exp.get("number", -1)):
        return "superseded"
    if frm and cur["tombstones_sha256"] == frm.get("tombstones_sha256"):
        if not ops.issuer.publish_tombstones():             # the CRL is ours, the tombstones were not promoted
            raise Refused("trust_publication.tombstones_failed")
        _crl, tombs, cur = ops._pair()
        if cur is None:
            raise Refused("trust_publication.pair_unreadable")
    if cur.get("content_sha256") != exp.get("content_sha256"):
        return "superseded"
    rec.update(result=cur, phase="published", published_at=round(ops.clock(), 3))
    _transition(ops, report, rec, "published", result=_short(cur))
    report["published"] = cur["revocation_list_number"]
    return "published"


def _advance(ops, report: dict, rec: dict) -> None:
    """Run (or resume) a publication record's LOCAL phases. Under the publication lock."""
    if rec["phase"] == "begun":
        crl, tombs, cur = ops._pair()
        if rec["from"] and not _same_pair(cur, rec["from"]):
            _close(ops, report, rec, SUPERSEDED, "the pair changed before the predecessor was saved")
            return
        if rec.get("from_verified"):
            _save_previous(ops, report, rec, crl, tombs)
        else:
            rec["previous"] = "unverifiable"
        rec["phase"] = "previous_saved"
        _transition(ops, report, rec, "previous_saved", previous=rec["previous"])
    if rec["phase"] == "previous_saved":
        state = _judge_after_publish(ops, report, rec)
        if state == "not_published":
            nxt = ops.issuer.next_publication()
            if nxt != rec["expect"]:                          # a failed attempt consumed the number, nothing is visible
                if not rec["security"] and rec["from"] and nxt["content_sha256"] != rec["from"].get("content_sha256"):
                    rec["security"] = True
                rec["expect"] = nxt
                _transition(ops, report, rec, "replanned", expect=nxt["number"])
            done = ops.issuer.publish()
            if not done.get("published"):
                report["published"] = "failed"
                raise Refused("trust_publication.publish_failed", "the issuer could not publish durably")
            state = _judge_after_publish(ops, report, rec)
        if state == "superseded":
            _close(ops, report, rec, SUPERSEDED, "the deployment's pair is neither the predecessor nor this record's")
            return
        if state == "not_published":
            raise Refused("trust_publication.publish_failed", "published, yet the pair did not change")


def _begin(ops, report: dict, reason: str, *, security: bool) -> dict:
    crl, tombs, cur = ops._pair()
    verified = True
    try:
        frm = verify_pair(ops, crl, tombs)
    except ValueError:
        verified, frm = False, cur
    if not security and not verified:
        raise Refused("trust_publication.current_unverifiable", "a rotation needs a verified predecessor")
    rec = {"schema": SCHEMA, "record_id": _next_id(ops.stand), "kind": PUBLICATION, "reason": reason,
           "security": bool(security), "created_at": round(ops.clock(), 3), "state": OPEN, "phase": "begun",
           "from": frm, "from_verified": verified, "expect": ops.issuer.next_publication(), "previous": None,
           "result": None, "targets": {}, "error": ""}
    if not security and frm and rec["expect"]["content_sha256"] != frm.get("content_sha256"):
        rec["security"] = True                                # the inventory holds more than the list: not a rotation
        rec["reason"] = reason + "+content"
    _transition(ops, report, rec, "begun", source=_short(frm), expect=rec["expect"]["number"])
    report["record"] = rec["record_id"]
    return rec


def local(ops, report: dict, *, force: bool = False, rotate: bool = True) -> None:
    """The bounded LOCAL phase, under the publication lock: settle a crashed list stage, resume every open
    publication record, publish a security change at once, and start a due rotation only when the guard
    allows it. Never touches the network."""
    try:
        open_pubs = _open(ops.stand, PUBLICATION)
    except ValueError as exc:
        raise Refused("trust_publication.journal_unreadable", str(exc)) from exc
    for rec in open_pubs:
        if rec["phase"] in ("begun", "previous_saved"):
            _advance(ops, report, rec)
    rc = ops.issuer.reconcile()
    if rc["reason"]:
        report["security"] = rc["reason"]
        rec = _begin(ops, report, "security:" + rc["reason"], security=True)
        _advance(ops, report, rec)
        return
    if not rotate:
        return
    crl, _tombs, cur = ops._pair()
    if cur is None:
        raise Refused("trust_publication.pair_unreadable")
    if _open(ops.stand, PUBLICATION):
        report["deferred"] = "an earlier publication is still being distributed"
        return
    now = ops.clock()
    born = _this_update(crl)
    if not force and born is not None and now - born < _rotate_after():
        return
    acks = ops.acks.load()
    lagging = sorted(t.target_id for t in _live(ops) if _trust_install.differences(acks.get(t.target_id), cur))
    left = min(float(cur["revocation_list_next_update"]), float(cur["tombstones_not_after"])) - now
    if lagging and left >= GUARD_RESERVE_SECONDS:
        report["deferred"] = "rotation waits: %s not on the current pair" % ",".join(lagging)
        report["alerts"].append("rotation_deferred (%s lagging, %d h left)" % (",".join(lagging), int(left // 3600)))
        return
    rec = _begin(ops, report, "forced_rotation" if force and (born is None or now - born < _rotate_after())
                 else "rotation", security=False)
    _advance(ops, report, rec)


# ---------------------------------------------------------------------- distribution
def _push_all(ops, report: dict, rec: dict, pair, targets: list) -> list[str]:
    from agentnode_sdk.remote_mcp.target_ops import OperationFailed
    lagging = []
    for t in sorted(targets, key=lambda t: t.target_id):
        try:
            ack = ops._push_trust(t, pair)
            rec["targets"][t.target_id] = "ok %d" % ack["revocation_list_number"]
            report["pushed"][t.target_id] = ack["revocation_list_number"]
        except OperationFailed as exc:
            rec["targets"][t.target_id] = "failed: " + exc.code + (" " + exc.detail if exc.detail else "")
            report["pushed"][t.target_id] = rec["targets"][t.target_id]
            lagging.append(t.target_id)
        except Exception as exc:                              # noqa: BLE001 - every failure is named
            rec["targets"][t.target_id] = "failed: " + type(exc).__name__
            report["pushed"][t.target_id] = rec["targets"][t.target_id]
            lagging.append(t.target_id)
        _write_record(ops.stand, rec)
        ops._crash_after("tp:pushed:" + t.target_id)
    return lagging


def distribute(ops, report: dict, *, wait: float) -> None:
    """Under the distribution lock, never the publication lock: give the deployment's ONE pair to the targets.
    An open publication record whose result is the current pair is distributed to every live target and
    closed; one whose result is no longer current is closed `superseded`; otherwise every live target not
    exactly on the current pair gets a `distribution` record of its own."""
    with _distribution_lock(ops, wait):
        pair = ops._pair()
        facts = pair[2]
        report["pair"] = _short(facts)
        if facts is None:
            raise Refused("trust_publication.pair_unreadable")
        for rec in [r for r in _open(ops.stand, DISTRIBUTION)]:
            with _publication_lock(ops):                      # a crashed retry: closed, a new one is journaled
                _close(ops, report, rec, DONE_DEGRADED, "interrupted; retried by a later distribution")
        live = _live(ops)
        for rec in _open(ops.stand, PUBLICATION):
            if rec["phase"] not in ("published", "distributing"):
                continue
            if not _same_pair(rec.get("result"), facts):
                with _publication_lock(ops):
                    _close(ops, report, rec, SUPERSEDED, "a later publication replaced this pair before distribution")
                continue
            if rec["phase"] == "published":
                rec["phase"] = "distributing"
                _transition(ops, report, rec, "distributing")
            lagging = _push_all(ops, report, rec, pair, live)
            report["lagging"] = lagging
            with _publication_lock(ops):
                _close(ops, report, rec, DONE_DEGRADED if lagging else DONE,
                       ("lagging " + ",".join(lagging)) if lagging else "")
            return
        acks = ops.acks.load()
        behind = [t for t in live if _trust_install.differences(acks.get(t.target_id), facts)]
        if not behind:
            return
        owner = next((r["record_id"] for r in reversed(load_records(ops.stand))
                      if r["kind"] == PUBLICATION and _same_pair(r.get("result"), facts)), "")
        rec = {"schema": SCHEMA, "record_id": "", "kind": DISTRIBUTION, "reason": "retry", "publication": owner,
               "created_at": round(ops.clock(), 3), "state": OPEN, "phase": "distributing", "from": None,
               "result": facts, "targets": {}, "error": ""}
        with _publication_lock(ops):
            rec["record_id"] = _next_id(ops.stand)
            _transition(ops, report, rec, "distributing", publication=owner)
        report["record"] = report.get("record") or rec["record_id"]
        lagging = _push_all(ops, report, rec, pair, behind)
        report["lagging"] = lagging
        with _publication_lock(ops):
            _close(ops, report, rec, DONE_DEGRADED if lagging else DONE,
                   ("lagging " + ",".join(lagging)) if lagging else "")


# ---------------------------------------------------------------------- one cycle
def new_report(entry: str) -> dict[str, Any]:
    return {"entry": entry, "published": None, "record": None, "pushed": {}, "lagging": [], "alerts": [],
            "pair": None, "deferred": "", "security": ""}


def cycle(ops, *, entry: str, force: bool = False, rotate: bool = True, distribute_wait: float | None = None,
          write_state: bool = False) -> dict:
    """One run of the publication authority: the local phase under the publication lock, then distribution.
    Raises OperationFailed / Refused for a named failure; `alerts`/`lagging` name a degraded result."""
    from agentnode_sdk.remote_mcp.target_ops import OperationFailed, deadline_alerts
    report = new_report(entry)
    wait = DISTRIBUTION_WAIT_SECONDS if distribute_wait is None else distribute_wait
    failure = ""
    try:
        with _publication_lock(ops):
            local(ops, report, force=force, rotate=rotate)
        distribute(ops, report, wait=wait)
        if report["deferred"].startswith("rotation waits") and not report["lagging"]:
            # the guard held the rotation for targets this cycle has just brought onto the current pair:
            # rotate now, once, and distribute what that published
            report["deferred"] = ""
            report["alerts"] = [a for a in report["alerts"] if not a.startswith("rotation_deferred")]
            with _publication_lock(ops):
                local(ops, report, force=force, rotate=rotate)
            distribute(ops, report, wait=wait)
        if write_state:
            report["reach"] = observe_reach(ops, report)
    except (OperationFailed, Refused) as exc:
        failure = exc.code + (" " + exc.detail if exc.detail else "")
        raise
    finally:
        report["alerts"] += deadline_alerts(ops.issuer.trust_dir, now=ops.clock(), lagging=report["lagging"])
        if write_state:
            _write_cycle_state(ops, report, failure)
    return report


def observe_reach(ops, report: dict) -> dict:
    """trust-watchdog D3: was every live target there in this run? A target this run pushed to with an EXACT
    acknowledgement was (`report["pushed"]` holds its list number); every other live target -- exact and not
    pushed, or whose push failed -- gets the router's handshake-only preflight (nothing sent, nothing changed),
    at most REACH_THREADS at once and all of them within REACH_DEADLINE_SECONDS; one not finished by then is
    `reach_deadline`. Returns {target_id: {"ok", "via", "code"}}; never raises, never touches the status."""
    from agentnode_sdk.remote_mcp.target_router import TargetRefusal, TargetRouter
    out: dict[str, dict] = {}
    try:
        live = _live(ops)
    except Exception as exc:                                  # noqa: BLE001 - named, never raised
        return {"*": {"ok": False, "via": "register", "code": "reach_error:" + type(exc).__name__}}
    todo = []
    for t in sorted(live, key=lambda t: t.target_id):
        pushed = report["pushed"].get(t.target_id)
        if isinstance(pushed, int) and not isinstance(pushed, bool):
            out[t.target_id] = {"ok": True, "via": "push", "code": ""}
        else:
            todo.append(t)
    if not todo:
        return out
    router = TargetRouter(register=ops.register, acks=ops.acks, worker_for=ops.worker_for,
                          deployment_trust=lambda: None)

    def one(t) -> dict:
        try:
            router.preflight(t, budget=REACH_BUDGET_SECONDS)
            return {"ok": True, "via": "preflight", "code": ""}
        except TargetRefusal as exc:
            return {"ok": False, "via": "preflight", "code": exc.code}
        except Exception as exc:                              # noqa: BLE001 - every failure is named
            return {"ok": False, "via": "preflight", "code": "reach_error:" + type(exc).__name__}

    pool = ThreadPoolExecutor(max_workers=max(1, min(REACH_THREADS, len(todo))), thread_name_prefix="reach")
    try:
        futures = {pool.submit(one, t): t.target_id for t in todo}
        done, _not_done = _wait(futures, timeout=REACH_DEADLINE_SECONDS)
        for f, tid in futures.items():
            out[tid] = f.result() if f in done else {"ok": False, "via": "preflight", "code": "reach_deadline"}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return out


def _write_cycle_state(ops, report: dict, failure: str) -> None:
    path = cycle_state_path(ops.stand)
    try:
        prev = load_cycle_state(ops.stand) or {}
    except ValueError:
        prev = {}
    status = "failed" if failure else ("degraded" if (report["alerts"] or report["lagging"]) else "ok")
    now = round(ops.clock(), 3)
    mono, boot = round(monotonic(), 3), current_boot_id()
    doc = {"schema": CYCLE_STATE_SCHEMA, "last_run": {"finished_at": now, "finished_mono": mono, "status": status,
                                                      "entry": report["entry"], "failure": failure,
                                                      "lagging": report["lagging"], "record": report["record"],
                                                      "published": report["published"]},
           "last_success_at": now if status == "ok" else prev.get("last_success_at"),
           # trust-watchdog D5: the same success on the monotonic clock of this boot
           "last_success_mono": mono if status == "ok" else prev.get("last_success_mono"),
           "boot_id": boot if status == "ok" else prev.get("boot_id"),
           "reach": _merge_reach(prev.get("reach"), report.get("reach"), now=now, mono=mono, boot=boot)}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, sort_keys=True, indent=1)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(str(tmp), 0o644)
    os.replace(str(tmp), str(path))
    _fsync_dir(path.parent)


def _merge_reach(prev, observed: dict | None, *, now: float, mono: float, boot: str | None) -> dict:
    """The reach record of every live target observed in this run: a success stamps `ok_at`/`ok_mono`/`boot_id`
    and ends the failure episode; a failure keeps the last success and the FIRST failure of the episode
    (`failing_since`) and names this run's code. A run that observed nothing (it failed) keeps what was there;
    a target no longer live is no longer listed."""
    prev = prev if isinstance(prev, dict) else {}
    if observed is None:
        return prev
    out = {}
    for tid, r in sorted(observed.items()):
        old = prev.get(tid) if isinstance(prev.get(tid), dict) else {}
        if r.get("ok"):
            out[tid] = {"ok_at": now, "ok_mono": mono, "boot_id": boot, "failing_since": None, "code": "",
                        "via": r.get("via", "")}
        else:
            out[tid] = {"ok_at": old.get("ok_at"), "ok_mono": old.get("ok_mono"), "boot_id": old.get("boot_id"),
                        "failing_since": old.get("failing_since") or now, "code": r.get("code") or "unknown",
                        "via": r.get("via", "")}
    return out


def unresolved_security(ops) -> list[str]:
    """Live targets whose acknowledged pair does not refuse what the current pair refuses (a security change
    not yet distributed) -- a rollback must not leave them to code that cannot finish the distribution."""
    _crl, _tombs, cur = ops._pair()
    if cur is None:
        return ["deployment pair unreadable"]
    acks = ops.acks.load()
    out = []
    for t in _live(ops):
        ack = acks.get(t.target_id)
        if not ack or not _trust_install.differences(ack, cur):
            continue
        pair = T.previous_pair(ops.stand)
        try:
            prev = _trust_install.facts_of(*pair) if pair else None
        except ValueError:
            prev = None
        if not (prev and not _trust_install.differences(ack, prev) and prev.get("content_sha256") == cur["content_sha256"]):
            out.append(t.target_id)
    return out


__all__ = ["CYCLE_STATE_SCHEMA", "DISTRIBUTION", "DISTRIBUTION_LOCK_NAME", "DISTRIBUTION_WAIT_SECONDS",
           "DISTRIBUTION_WINDOW_SECONDS", "DONE", "DONE_DEGRADED", "GUARD_RESERVE_SECONDS", "HUNG_AFTER_SECONDS",
           "OPEN", "POINTER_SCHEMA", "PUBLICATION", "ROTATE_AFTER_SECONDS", "Refused", "SCHEMA", "SUPERSEDED",
           "TERMINAL", "cycle", "cycle_state_path", "distribute", "import_legacy", "load_cycle_state",
           "load_pointer", "load_records", "local", "materialize_legacy", "pointer_path", "records_dir",
           "unresolved_security", "verify_pair"]
