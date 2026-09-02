"""EM-3A — the common sandbox contract shared by every backend.

One contract, three backends (local container, self-hosted gateway, managed MicroVM). Frozen scope:
``EXEC_MODEL_PLAN_EM3.md`` v1.4, sha256 958ac29f20d38a311091c80197e763618ada6b4a186163290d3f90a3284667a9.

Two design rules run through everything here, and both are enforced by construction rather than by
convention, because a rule that lives only in prose is one refactor away from being gone:

* **Security**: a lower-precedence policy scope may only NARROW. Package and agent metadata come from
  the untrusted side and can never widen what the user or the organisation allowed.
* **Usability**: a user-visible refusal is not representable without at least one executable way out.
  Human-facing text is part of THIS contract, not of some later UI — a backend that can only answer
  with an error code makes a comprehensible interface impossible no matter how good the frontend is.

Nothing here starts a process. This module is types, invariants and one selection gate; the backends
that implement it are EM-3C and later.
"""
from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping, Sequence


# --------------------------------------------------------------------------------------
# Assurance — a closed vocabulary of exactly three words (founder decision D6)
# --------------------------------------------------------------------------------------

class AssuranceLevel(str, Enum):
    """How a claim about a backend is backed. There is no fourth value.

    ``certified`` is deliberately absent: without an independent certification the word is not
    available anywhere in the product, and an enum cannot grow one by accident.
    """

    SELF_REPORTED = "self_reported"   # the backend asserts it
    OBSERVED = "observed"             # the conformance suite measured it (EM-3B), signed, bound to a version
    ATTESTED = "attested"             # a platform attestation or independent audit backs it

    def at_least(self, floor: "AssuranceLevel") -> bool:
        order = [AssuranceLevel.SELF_REPORTED, AssuranceLevel.OBSERVED, AssuranceLevel.ATTESTED]
        return order.index(self) >= order.index(floor)


# --------------------------------------------------------------------------------------
# Actions — the difference between "here is a button" and "here is a leaflet"
# --------------------------------------------------------------------------------------

class ActionKind(str, Enum):
    REMEDIATION = "remediation"       # changes the situation; counts as a way out
    INFORMATIONAL = "informational"   # explains it; NEVER counts as a way out


@dataclass(frozen=True)
class Action:
    """Something the person in front of the screen can actually do next."""

    id: str
    label: str                        # plain language, no jargon
    kind: ActionKind

    def __post_init__(self) -> None:
        if not self.id or not self.label.strip():
            raise ValueError("an action needs an id and a non-empty plain-language label")


# The catalogue. `learn_more` and `show_details` are informational ON PURPOSE: a link to
# documentation is not a way out of a dead end, and counting it as one is how dead ends get shipped.
USE_MANAGED = Action("use_managed", "Use the AgentNode Sandbox", ActionKind.REMEDIATION)
INSTALL_LOCAL_RUNTIME = Action("install_local_runtime", "Set up secure execution on this device",
                               ActionKind.REMEDIATION)
CONNECT_OWN_SERVER = Action("connect_own_server", "Connect your own server", ActionKind.REMEDIATION)
RETRY_WHEN_ONLINE = Action("retry_when_online", "Try again when you are back online",
                           ActionKind.REMEDIATION)
CONTACT_SUPPORT = Action("contact_support", "Get help", ActionKind.REMEDIATION)
LEARN_MORE = Action("learn_more", "What does this mean?", ActionKind.INFORMATIONAL)
SHOW_DETAILS = Action("show_details", "Show technical details", ActionKind.INFORMATIONAL)


def _require_a_way_out(actions: Sequence[Action], what: str) -> tuple[Action, ...]:
    if not any(a.kind is ActionKind.REMEDIATION for a in actions):
        raise ValueError(
            f"{what} must offer at least one executable remediation. "
            f"Informational actions such as {LEARN_MORE.id!r} do not count — a refusal a person "
            f"cannot act on is a contract violation, not a UI defect."
        )
    return tuple(actions)


@dataclass(frozen=True)
class Refusal:
    """Execution did not happen, and here is what you can do about it.

    Cardinality zero is unrepresentable: ``__post_init__`` rejects an action list with no
    remediation, so a dead-end refusal cannot be constructed, let alone returned.
    """

    code: str                          # for logs and support, never shown alone
    reason: str                        # plain language, shown to the person
    actions: tuple[Action, ...]

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("a refusal must carry a plain-language reason")
        object.__setattr__(self, "actions", _require_a_way_out(self.actions, "a refusal"))

    @property
    def remediations(self) -> tuple[Action, ...]:
        return tuple(a for a in self.actions if a.kind is ActionKind.REMEDIATION)


@dataclass(frozen=True)
class Blocked:
    """A state nothing can currently fix — modelled separately rather than as an empty refusal.

    Even here the person gets two things: a clean way to abandon the job with nothing left behind,
    and a way to reach a human.
    """

    code: str
    reason: str
    safe_exit: Action = field(default=Action("abandon_safely", "Stop and keep nothing",
                                             ActionKind.REMEDIATION))
    escalation: Action = CONTACT_SUPPORT

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("a blocked state must carry a plain-language reason")
        for a in (self.safe_exit, self.escalation):
            if a.kind is not ActionKind.REMEDIATION:
                raise ValueError("a blocked state's exit and escalation must both be executable")


@dataclass(frozen=True)
class Capability:
    """One thing a backend can or cannot do, in words a person understands."""

    id: str
    human_name: str                    # "Runs isolated on this device", not "oci/runc cap-drop=ALL"
    met: bool
    reason: str = ""                   # required when not met
    actions: tuple[Action, ...] = ()

    def __post_init__(self) -> None:
        if not self.met:
            if not self.reason.strip():
                raise ValueError(f"capability {self.id!r} is unmet and must say why, in plain language")
            object.__setattr__(self, "actions",
                               _require_a_way_out(self.actions, f"unmet capability {self.id!r}"))


# --------------------------------------------------------------------------------------
# Policy — and the rule that a lower scope may only narrow (D7)
# --------------------------------------------------------------------------------------

class Scope(int, Enum):
    """Precedence. Higher wins, and lower may only narrow what higher allowed."""

    PACKAGE = 0          # publisher/agent metadata — untrusted
    PROJECT = 1
    USER = 2
    ORGANISATION = 3


@dataclass(frozen=True)
class Limits:
    cpu: float = 1.0
    memory_mb: int = 512
    processes: int = 256
    disk_mb: int = 512
    wall_clock_s: int = 180

    def _narrowed_by(self, other: "Limits") -> "Limits":
        return Limits(min(self.cpu, other.cpu), min(self.memory_mb, other.memory_mb),
                      min(self.processes, other.processes), min(self.disk_mb, other.disk_mb),
                      min(self.wall_clock_s, other.wall_clock_s))


@dataclass(frozen=True)
class NetworkRules:
    """Default off. An allowlist can only ever shrink as scopes descend."""

    enabled: bool = False
    allowed_destinations: frozenset[str] = frozenset()

    def _narrowed_by(self, other: "NetworkRules") -> "NetworkRules":
        return NetworkRules(self.enabled and other.enabled,
                            self.allowed_destinations & other.allowed_destinations)


@dataclass(frozen=True)
class SecretRef:
    """A reference. The value never appears in this object, in the job, or in the sandbox."""

    name: str
    inject_hosts: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.name):
            raise ValueError(f"not a usable secret name: {self.name!r}")


@dataclass(frozen=True)
class Retention:
    """Founder decision D5, expressed as data rather than as documentation."""

    workspace: str = "destroy_on_result_handover"
    diagnostics_hours: int = 24
    audit_metadata_days: int = 30

    def _narrowed_by(self, other: "Retention") -> "Retention":
        return Retention(self.workspace,
                         min(self.diagnostics_hours, other.diagnostics_hours),
                         min(self.audit_metadata_days, other.audit_metadata_days))


@dataclass(frozen=True)
class SandboxPolicy:
    limits: Limits = field(default_factory=Limits)
    network: NetworkRules = field(default_factory=NetworkRules)
    secrets: tuple[SecretRef, ...] = ()
    region: str | None = None
    retention: Retention = field(default_factory=Retention)
    required_assurance: AssuranceLevel = AssuranceLevel.OBSERVED

    def _narrowed_by(self, lower: "SandboxPolicy") -> "SandboxPolicy":
        """Fold a LOWER-precedence policy in. It can tighten anything and loosen nothing.

        Injection targets are intersected with the egress allowlist here rather than validated
        later: a credential can only ever be injected into a connection the network policy already
        admits, so the two can never drift apart.
        """
        net = NetworkRules._narrowed_by(self.network, lower.network)
        secrets = tuple(
            replace(s, inject_hosts=frozenset(s.inject_hosts) & net.allowed_destinations)
            for s in self.secrets
            if s.name in {t.name for t in lower.secrets} or not lower.secrets
        )
        return SandboxPolicy(
            limits=Limits._narrowed_by(self.limits, lower.limits),
            network=net,
            secrets=secrets,
            region=self.region,                     # only a higher scope sets a region
            retention=Retention._narrowed_by(self.retention, lower.retention),
            # a lower scope may RAISE the assurance floor, never lower it
            required_assurance=max(self.required_assurance, lower.required_assurance,
                                   key=lambda a: [AssuranceLevel.SELF_REPORTED,
                                                  AssuranceLevel.OBSERVED,
                                                  AssuranceLevel.ATTESTED].index(a)),
        )


def merge_policies(scoped: Mapping[Scope, SandboxPolicy]) -> SandboxPolicy:
    """Fold from the highest scope downwards, so every lower scope can only tighten.

    Two things make this a boundary rather than an API convention:

    * **exact types only.** A subclass could override ``_narrowed_by`` and widen instead of narrow,
      so anything that is not exactly a :class:`SandboxPolicy` — and whose parts are not exactly the
      expected types — is rejected rather than trusted. Untrusted lower-scope input arrives as data,
      and data is what this accepts.
    * **no virtual dispatch.** The fold calls the unbound functions explicitly, so even a subclass
      that slipped past the check could not redirect the composition.
    """
    for scope, policy in scoped.items():
        if type(policy) is not SandboxPolicy:
            raise TypeError(
                f"policy for scope {scope!r} is {type(policy).__name__}, not exactly SandboxPolicy; "
                "a subclass could redefine narrowing and widen what a lower scope may do"
            )
        for part, expected in ((policy.limits, Limits), (policy.network, NetworkRules),
                               (policy.retention, Retention)):
            if type(part) is not expected:
                raise TypeError(
                    f"policy for scope {scope!r} carries a {type(part).__name__} where "
                    f"{expected.__name__} is required"
                )
    ordered = sorted(scoped.items(), key=lambda kv: kv[0], reverse=True)
    if not ordered:
        return SandboxPolicy()
    merged = ordered[0][1]
    for _scope, policy in ordered[1:]:
        merged = SandboxPolicy._narrowed_by(merged, policy)
    return merged


# --------------------------------------------------------------------------------------
# Remote consent — bound, revocable, and never silently reused (E3)
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ConsentBinding:
    account: str
    operator: str
    backend_id: str
    data_classes: frozenset[str]
    region: str
    retention: Retention
    policy_version: str


@dataclass(frozen=True)
class RemoteConsent:
    binding: ConsentBinding
    granted_at: str
    revoked: bool = False

    def covers(self, wanted: ConsentBinding) -> bool:
        """A stored consent is valid only while every bound element still matches.

        Retention is compared as "no longer than agreed": shortening it needs no new consent,
        lengthening it does.
        """
        if self.revoked:
            return False
        b = self.binding
        return (b.account == wanted.account
                and b.operator == wanted.operator
                and b.backend_id == wanted.backend_id
                and wanted.data_classes <= b.data_classes
                and b.region == wanted.region
                and b.policy_version == wanted.policy_version
                # the retention BASIS is a material term, not a number: changing when the
                # workspace dies changes what was agreed to, so it must match exactly
                and b.retention.workspace == wanted.retention.workspace
                and wanted.retention.diagnostics_hours <= b.retention.diagnostics_hours
                and wanted.retention.audit_metadata_days <= b.retention.audit_metadata_days)


# --------------------------------------------------------------------------------------
# The legacy host mode: a capability nothing automatic can mint (D3 / E6)
# --------------------------------------------------------------------------------------

class LegacyHostIntent:
    """Proof that a human deliberately confirmed host execution, for exactly one bound operation.

    `EM3A-IMPL-0001` / F-A2 found the first version forgeable: it was a duck type, so any object with
    a ``spend()`` method selected host execution, and the mint key was reachable on the module. The
    capability now lives in a private registry the gate checks; an instance on its own proves nothing.

    What a holder gets is a handle. What the gate verifies is that the handle's token is in the
    registry, unspent, unexpired, and bound to the operation being attempted. Forging the object does
    not put a token in the registry, and the registry is not reachable through the public surface.
    """

    __slots__ = ("_token", "_purpose", "_confirmed_by", "_confirmed_at")

    def __init__(self, _private: object, purpose: str, confirmed_by: str, confirmed_at: float) -> None:
        if _private is not _MINT_KEY:
            raise PermissionError(
                "LegacyHostIntent cannot be constructed directly. It records a deliberate human "
                "confirmation, and an automatic path must not be able to fabricate one."
            )
        self._token = secrets.token_urlsafe(32)
        self._purpose = purpose
        self._confirmed_by = confirmed_by
        self._confirmed_at = confirmed_at

    @property
    def purpose(self) -> str:
        return self._purpose

    @property
    def confirmed_by(self) -> str:
        return self._confirmed_by

    @property
    def confirmed_at(self) -> float:
        return self._confirmed_at


_MINT_KEY = object()
_LIVE_INTENTS: dict[str, tuple[str, float]] = {}   # token -> (purpose, confirmed_at)
INTENT_TTL_SECONDS = 300.0

LEGACY_HOST_WARNING = (
    "Host execution with elevated risk: code from other people would run directly on this computer, "
    "outside the protected area. Only continue if you know exactly what you are running."
)


def mint_legacy_host_intent(confirm: Any, *, purpose: str, confirmed_by: str,
                            now: float | None = None) -> LegacyHostIntent:
    """Mint one confirmation, bound to one named operation, from an interactive flow only.

    ``purpose`` names the run or settings change being confirmed, so a confirmation for one thing
    cannot be spent on another. ``confirm`` must be a callable that shows
    :data:`LEGACY_HOST_WARNING` and returns ``True`` only for an explicit human confirmation.

    The gate rejects anything that is not a registered, unspent, unexpired token, so an automatic
    caller cannot get past it by imitating the object, by reaching for the mint key, or by keeping an
    old handle around.
    """
    if not callable(confirm):
        raise PermissionError("an explicit interactive confirmation is required")
    if not purpose:
        raise PermissionError("a confirmation must name the operation it confirms")
    if confirm(LEGACY_HOST_WARNING) is not True:
        raise PermissionError("host execution was not confirmed")
    stamp = time.monotonic() if now is None else now
    intent = LegacyHostIntent(_MINT_KEY, purpose, confirmed_by, stamp)
    _LIVE_INTENTS[intent._token] = (purpose, stamp)
    return intent


def _consume_intent(intent: object, purpose: str, now: float | None = None) -> None:
    """Verify and burn a confirmation, or refuse. Everything unusual is a refusal, never a pass."""
    if type(intent) is not LegacyHostIntent:
        raise PermissionError(
            "that is not a confirmation this gate issued; host execution needs a fresh confirmation "
            "made by a person in advanced settings"
        )
    token = object.__getattribute__(intent, "_token")
    entry = _LIVE_INTENTS.pop(token, None)
    if entry is None:
        raise PermissionError("this confirmation was already used, or was never issued")
    bound_purpose, stamp = entry
    if bound_purpose != purpose:
        raise PermissionError(
            f"this confirmation was given for {bound_purpose!r}, not for {purpose!r}"
        )
    elapsed = (time.monotonic() if now is None else now) - stamp
    if elapsed > INTENT_TTL_SECONDS:
        raise PermissionError("this confirmation is too old; confirm again if you still want this")


# --------------------------------------------------------------------------------------
# The single selection gate
# --------------------------------------------------------------------------------------

class Placement(str, Enum):
    LOCAL = "local"                  # a container on this device
    SELF_HOSTED = "self_hosted"      # the customer's own gateway
    MANAGED = "managed"              # the AgentNode Sandbox
    LEGACY_HOST = "legacy_host"      # unprotected; reachable only with a LegacyHostIntent


@dataclass(frozen=True)
class Selection:
    placement: Placement
    human_name: str
    consent_required: bool = False   # a remote placement never proceeds on a stale consent


@dataclass(frozen=True)
class Environment:
    """What the gate is allowed to know. Deliberately small."""

    local_runtime_ready: bool = False
    is_mobile: bool = False
    organisation_backend: str | None = None
    managed_available: bool = True
    online: bool = True
    consent: RemoteConsent | None = None
    wanted_binding: ConsentBinding | None = None
    config_host_trust_policy: str = "curated_only"   # may say "default"; on its own it selects nothing


def select_backend(env: Environment, *, intent: object | None = None,
                   purpose: str = "") -> Selection | Refusal:
    """The one authoritative gate. Every path — automatic, onboarding, fallback, retry — comes here.

    Returns a :class:`Selection`, or a :class:`Refusal` that always carries a way out. It never
    returns ``None`` and never falls through to host execution.
    """
    if intent is not None:
        _consume_intent(intent, purpose)
        return Selection(Placement.LEGACY_HOST, "On this device, unprotected")

    if env.organisation_backend:
        return Selection(Placement.SELF_HOSTED, "Your organisation's server",
                         consent_required=_needs_consent(env))

    if env.local_runtime_ready and not env.is_mobile:
        return Selection(Placement.LOCAL, "On this device")

    if env.managed_available and env.online:
        return Selection(Placement.MANAGED, "AgentNode Sandbox",
                         consent_required=_needs_consent(env))

    if not env.online:
        return Refusal(
            "offline_no_secure_placement",
            "You are offline, and secure execution on this device is not set up yet. "
            "Nothing was run.",
            (INSTALL_LOCAL_RUNTIME, RETRY_WHEN_ONLINE, LEARN_MORE),
        )

    return Refusal(
        "no_secure_placement",
        "There is no safe place to run this yet. Nothing was run on your computer.",
        (USE_MANAGED, INSTALL_LOCAL_RUNTIME, CONNECT_OWN_SERVER, LEARN_MORE),
    )


def _needs_consent(env: Environment) -> bool:
    if env.wanted_binding is None:
        return True
    return not (env.consent is not None and env.consent.covers(env.wanted_binding))


# --------------------------------------------------------------------------------------
# Capability negotiation — refuse before start, never after
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class BackendCapabilities:
    backend_id: str
    human_name: str
    assurance: AssuranceLevel
    capabilities: tuple[Capability, ...] = ()
    regions: frozenset[str] = frozenset()

    def unmet(self) -> tuple[Capability, ...]:
        return tuple(c for c in self.capabilities if not c.met)


def negotiate(policy: SandboxPolicy, caps: BackendCapabilities) -> Refusal | None:
    """``None`` means the job may start. Anything else is a refusal with a way out.

    A backend may exceed the floor freely — only falling below it refuses. That is what keeps the
    managed tier from being levelled down to the weakest backend's guarantees.
    """
    if not caps.assurance.at_least(policy.required_assurance):
        return Refusal(
            "assurance_below_requirement",
            f"{caps.human_name} cannot yet prove the level of protection this job requires.",
            (USE_MANAGED, CONNECT_OWN_SERVER, LEARN_MORE),
        )
    if policy.region and caps.regions and policy.region not in caps.regions:
        return Refusal(
            "region_unavailable",
            f"{caps.human_name} cannot run this in {policy.region}.",
            (USE_MANAGED, CONNECT_OWN_SERVER, LEARN_MORE),
        )
    unmet = caps.unmet()
    if unmet:
        first = unmet[0]
        return Refusal(
            "capability_missing",
            f"{caps.human_name} cannot do this safely: {first.reason}",
            first.actions,
        )
    return None
