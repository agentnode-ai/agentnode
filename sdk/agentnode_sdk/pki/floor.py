"""The time floor: a point in time the services may never go back behind, written only by root.

Decision 5.7. A clock that was set back cannot be caught with the same clock. So every validity
judgement a service makes -- of a certificate, of a revocation list -- is made at the EFFECTIVE
time, the later of the system clock and this floor, and the floor only moves forward.

## Who writes it

Root does, from the same root run that revokes and issues (`agentnode pki tick`, on a timer).
The services READ it on every judgement and never write it. A file a service owns is a file it can
truncate or overwrite, and a floor a service can reset is no floor, so:

    /var/lib/agentnode-floor/        root:root 0755
      gateway.floor                  root:root 0644   the gateway reads it, and that is all
      worker.floor                   root:root 0644   the worker reads it, and that is all

The decision names `/var/lib/agentnode/floor/`. That directory cannot hold it here: `/var/lib/
agentnode` is the gateway's own state directory (systemd `StateDirectory=agentnode`, mode 0700,
owned by the gateway account), so the gateway could rename a root-owned `floor/` out of the way
and put one of its own in its place, and the worker's unit makes the whole directory inaccessible
to the worker. `/var/lib/agentnode-floor` sits directly under root's `/var/lib`, where neither
service can write, rename or replace anything.

## How far it may move

Not by steps, and not by whatever the system clock says, but by time the machine actually ran,
plus ONE tolerance granted once at setup. The file holds, besides the floor, a per-boot ANCHOR
(boot identity, monotonic reading at the first write in that boot, and the elapsed total at that
moment) and two LIFETIME counters that are never reset:

    elapsed_total   monotonic running time summed over every boot
    granted_total   how much the system clock has ever actually moved the floor

    granted_total  <=  elapsed_total + tolerance          (less than or equal)

`advance` is the seven steps of decision 5.7, one line each, in that order. What a signed list
contributes (its `thisUpdate`) may raise the floor further; it is signed, so it is not the clock.

## When a service stops trusting it

A floor is only worth something while it is kept up. Each file carries its maximum age, and a
service treats a floor as unusable -- and refuses every connection -- when

    * the file is missing, unreadable or does not parse (never a silent fresh start: only root
      writes it, so this is not a state a service can bring about);
    * it has not yet been written in THIS boot -- the kernel's boot identity differs, or it is the
      initial state -- because nobody can say how long the machine was off;
    * it is older than its maximum age, measured on the MONOTONIC clock, which setting the system
      clock does not move.

A root run that cannot write therefore takes the services down after the maximum age, and so does
a revocation that cannot be published: the root run promotes no new floor while one is pending.
That is deliberate (decision 5.6).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

DEFAULT_DIR = "/var/lib/agentnode-floor"

#: The one allowance the system clock gets beyond the time the machine ran, for its whole life.
DEFAULT_TOLERANCE_SECONDS = 600.0
#: How old a floor may be before the services stop trusting it. Much longer than the root run's
#: interval (60 s in the shipped timer), so a late tick does nothing and a dead writer shows.
DEFAULT_MAX_AGE_SECONDS = 900.0

FORMAT = 1
ROLES = ("gateway", "worker")

# Why a floor was not usable, as a refusal states it.
MISSING = "time-floor-missing"
UNREADABLE = "time-floor-unreadable"
NOT_WRITTEN = "time-floor-not-written-in-this-boot"
OTHER_BOOT = "time-floor-from-another-boot"
TOO_OLD = "time-floor-too-old"
NO_BOOT = "time-floor-no-boot-identity"


# -- the three clocks, as seams. A test sets them; nothing else does. -------------------------

def _system_now() -> float:
    return time.time()


def _monotonic() -> float:
    return time.monotonic()


def _boot() -> str:
    from agentnode_sdk.gateway.lifecycle import this_boot

    return this_boot()


class FloorUnusable(Exception):
    """This side has no floor it can trust, so it judges nothing. Carries which reason."""

    def __init__(self, check: str, detail: str) -> None:
        self.check = check
        self.detail = detail
        super().__init__("%s: %s" % (check, detail))


@dataclass(frozen=True)
class FloorState:
    role: str
    generation: int
    floor: float
    #: The anchor of the boot this file was last written in. "" before the first write.
    boot_id: str
    anchor_monotonic: float
    elapsed_at_boot_start: float
    #: The two lifetime counters.
    elapsed_total: float
    granted_total: float
    tolerance_s: float
    max_age_s: float
    #: The monotonic reading at the last successful write, in `boot_id`. None before the first.
    #: The floor's AGE is measured from this, and only from this.
    written_monotonic: float | None
    #: The system clock at the last write. For a person reading the file -- NEVER for the age:
    #: the system clock is the one a rollback moves, and a deadline it could move is no deadline.
    written_at: float | None = None

    def to_bytes(self) -> bytes:
        body = {"format": FORMAT, **asdict(self)}
        return (json.dumps(body, indent=1, sort_keys=True) + "\n").encode("ascii")


_FIELDS = {"role": str, "generation": int, "floor": float, "boot_id": str,
           "anchor_monotonic": float, "elapsed_at_boot_start": float, "elapsed_total": float,
           "granted_total": float, "tolerance_s": float, "max_age_s": float}


def parse(data: bytes) -> FloorState:
    """The state in `data`, or `FloorUnusable(UNREADABLE)`. Strict: a field missing, a field too
    many, or a value of the wrong kind is damage, and damage is not read around."""
    try:
        body = json.loads(data.decode("ascii"))
        if not isinstance(body, dict) or body.get("format") != FORMAT:
            raise ValueError("not a floor of format %d" % FORMAT)
        keys = set(body) - {"format"}
        wanted = set(_FIELDS) | {"written_monotonic", "written_at"}
        if keys != wanted:
            raise ValueError("fields differ from the format")
        values = {}
        for name, kind in _FIELDS.items():
            value = body[name]
            if kind is float and isinstance(value, int) and not isinstance(value, bool):
                value = float(value)
            if not isinstance(value, kind) or isinstance(value, bool):
                raise ValueError("%s is not a %s" % (name, kind.__name__))
            values[name] = value
        stamps = {}
        for name in ("written_monotonic", "written_at"):
            value = body[name]
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("%s is not a number" % name)
                value = float(value)
            stamps[name] = value
        if values["role"] not in ROLES:
            raise ValueError("unknown role")
        return FloorState(**stamps, **values)
    except (ValueError, UnicodeDecodeError, TypeError) as exc:
        raise FloorUnusable(UNREADABLE, "the floor file does not parse (%s)" % exc) from exc


def initial(role: str, ca_not_before: float, *, tolerance_s: float = DEFAULT_TOLERANCE_SECONDS,
            max_age_s: float = DEFAULT_MAX_AGE_SECONDS) -> FloorState:
    """The state at setup: the floor at the CA certificate's notBefore -- signed and local, not a
    clock reading -- both counters at zero, no anchor, never written in any boot. The first case
    is this CONTENT, not the absence of a file."""
    if role not in ROLES:
        raise ValueError("a floor belongs to the gateway or the worker")
    if not float(max_age_s) > 0 or float(tolerance_s) < 0:
        raise ValueError("the maximum age must be positive and the tolerance not negative")
    return FloorState(role=role, generation=0, floor=float(ca_not_before), boot_id="",
                      anchor_monotonic=0.0, elapsed_at_boot_start=0.0, elapsed_total=0.0,
                      granted_total=0.0, tolerance_s=float(tolerance_s),
                      max_age_s=float(max_age_s), written_monotonic=None, written_at=None)


def advance(state: FloorState, *, system_now: float, monotonic_now: float, boot: str,
            list_this_update: float | None) -> FloorState:
    """One fortschreibung by the root run: decision 5.7's seven steps, literally.

    `list_this_update` is the `thisUpdate` of the currently valid signed list, or None.
    """
    if not boot:
        raise FloorUnusable(NO_BOOT, "this machine gives no boot identity, so no anchor can be "
                            "set and no time can be credited")
    # The anchor of this boot: set once, at the first write in the boot, and not touched again
    # until the boot changes. A new boot brings no time: the elapsed total carries over as it was.
    if state.boot_id != boot:
        state = replace(state, boot_id=boot, anchor_monotonic=float(monotonic_now),
                        elapsed_at_boot_start=state.elapsed_total)

    # 1. verstrichen_neu = verstrichen_bei_bootbeginn + (monoton_jetzt - monoton_anker)
    elapsed_new = state.elapsed_at_boot_start + (float(monotonic_now) - state.anchor_monotonic)
    # 2. spielraum = verstrichen_neu + toleranz - zugestanden_gesamt          (nie negativ)
    headroom = max(0.0, elapsed_new + state.tolerance_s - state.granted_total)
    # 3. uhr_vorschlag = max(0, systemuhr_jetzt - boden_alt)
    clock_offer = max(0.0, float(system_now) - state.floor)
    # 4. uhr_zugelassen = min(uhr_vorschlag, spielraum)
    clock_allowed = min(clock_offer, headroom)
    # 5. boden_neu = max(boden_alt + uhr_zugelassen, thisUpdate_der_gueltigen_liste)
    floor_new = state.floor + clock_allowed
    if list_this_update is not None:
        floor_new = max(floor_new, float(list_this_update))
    # 6. zugestanden_gesamt wird um uhr_zugelassen erhoeht, und um nichts sonst
    granted_new = state.granted_total + clock_allowed
    # 7. verstrichen_gesamt wird auf verstrichen_neu gesetzt
    return replace(state, generation=state.generation + 1, floor=floor_new,
                   granted_total=granted_new, elapsed_total=elapsed_new,
                   written_monotonic=float(monotonic_now), written_at=float(system_now))


def recover(state: FloorState, target_floor: float) -> FloorState:
    """Root's recovery of a floor that stands too far ahead: the floor is set to `target_floor`
    (the later of the CA's notBefore and the valid list's thisUpdate -- both signed), and NOTHING
    ELSE changes. In particular neither lifetime counter: what the clock has contributed stays
    counted and the tolerance stays as spent as it was. The next root run writes it in this boot
    as usual; until then a service treats it exactly as it treated it before."""
    return replace(state, generation=state.generation + 1, floor=float(target_floor))


def newer(staged: bytes, current: bytes | None) -> bool:
    """For `files.settle_stage`: is a leftover stage newer than what the services read?"""
    try:
        stage_generation = parse(staged).generation
    except FloorUnusable:
        return False
    if current is None:
        return True
    try:
        return stage_generation > parse(current).generation
    except FloorUnusable:
        return True


# ---------------------------------------------------------------------- the services' side

def judge(data: bytes | None, *, role: str, boot: str, monotonic_now: float) -> float:
    """The floor in `data`, if this side may use it now; `FloorUnusable` saying why not.

    Nothing here writes, and nothing here repairs: every reason below is a refusal.
    """
    if data is None:
        raise FloorUnusable(MISSING, "there is no floor file")
    state = parse(data)
    if state.role != role:
        raise FloorUnusable(UNREADABLE, "the floor file belongs to the %s, not the %s"
                            % (state.role, role))
    if not boot:
        raise FloorUnusable(NO_BOOT, "this machine gives no boot identity, so the floor's age "
                            "cannot be established")
    if state.written_monotonic is None or not state.boot_id:
        raise FloorUnusable(NOT_WRITTEN, "the floor has never been written by the root run")
    if state.boot_id != boot:
        raise FloorUnusable(OTHER_BOOT, "the floor was last written in another boot; nobody can "
                            "say how long the machine was off, so it waits for the root run to "
                            "write it in this one")
    # Same boot, so both readings are on the same monotonic clock and the difference is real.
    age = float(monotonic_now) - state.written_monotonic
    if age < 0:
        raise FloorUnusable(UNREADABLE, "the floor claims a write in this boot's future")
    if age > state.max_age_s:
        raise FloorUnusable(TOO_OLD, "the floor is %d seconds old and may be at most %d; the "
                            "root run has not kept it up" % (int(age), int(state.max_age_s)))
    return state.floor


def read(path, role: str) -> float:
    """`judge` on the file at `path`, now. Opens it for reading and for nothing else."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        data = None
    except OSError as exc:
        raise FloorUnusable(UNREADABLE, "the floor file could not be read (%s)"
                            % type(exc).__name__) from exc
    return judge(data, role=role, boot=_boot(), monotonic_now=_monotonic())


def effective_time(floor_value: float) -> float:
    """The time every validity judgement is made at: the later of the system clock and the
    floor. A clock set back cannot take it back behind the floor."""
    return max(_system_now(), float(floor_value))


def path_for(directory, role: str) -> Path:
    return Path(directory) / (role + ".floor")


__all__ = ["DEFAULT_DIR", "DEFAULT_MAX_AGE_SECONDS", "DEFAULT_TOLERANCE_SECONDS", "FloorState",
           "FloorUnusable", "MISSING", "NOT_WRITTEN", "NO_BOOT", "OTHER_BOOT", "ROLES", "TOO_OLD",
           "UNREADABLE", "advance", "effective_time", "initial", "judge", "newer", "parse",
           "path_for", "read", "recover"]
