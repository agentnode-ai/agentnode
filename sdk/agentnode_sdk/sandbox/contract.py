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


# Every remediation declares where it LEADS. That is what makes it a way out rather than a label:
# the check below resolves each entry to a protected placement or to stopping, so an entry that
# resolves to neither cannot exist.
LEADS_TO_PROTECTED = "protected_placement"
LEADS_TO_STOPPING = "stopping"

_REMEDIATION_CATALOGUE: dict[str, Action] = {}
_REMEDIATION_OUTCOME: dict[str, str] = {}
_CATALOGUE_SEALED = False


def _register_remediation(action: Action, leads_to: str) -> Action:
    """Add an action the product actually implements. Module-private, and sealed after import.

    `EM3A-IMPL-0002` / F-A1: a caller could previously construct any `Action` and label it a
    remediation, so the invariant proved a *label* rather than a way out. Only catalogue entries
    count now, and the catalogue is what a front end has to implement — an id nobody wired up cannot
    be smuggled into a refusal to satisfy the check.
    """
    if _CATALOGUE_SEALED:
        raise RuntimeError(
            "the remediation catalogue is sealed. It is fixed at import so a caller cannot invent a "
            "way out that nothing implements, and cannot mutate one that exists."
        )
    if action.kind is not ActionKind.REMEDIATION:
        raise ValueError("only remediation actions belong in the catalogue")
    if leads_to not in (LEADS_TO_PROTECTED, LEADS_TO_STOPPING):
        raise ValueError(
            f"{action.id!r} must declare where it leads: a protected placement, or stopping"
        )
    blob = (action.id + " " + action.label).lower()
    for word in ("host", "unprotected", "unsafe", "legacy", "directly on"):
        if word in blob:
            raise ValueError(
                f"{action.id!r} points at host execution. Every way out of a refusal ends in a "
                "protected placement or in stopping; this contract has no host placement."
            )
    _REMEDIATION_CATALOGUE[action.id] = action
    _REMEDIATION_OUTCOME[action.id] = leads_to
    return action


# The catalogue. `learn_more` and `show_details` are informational ON PURPOSE: a link to
# documentation is not a way out of a dead end, and counting it as one is how dead ends get shipped.
USE_MANAGED = _register_remediation(
    Action("use_managed", "Use the AgentNode Sandbox", ActionKind.REMEDIATION), LEADS_TO_PROTECTED)
INSTALL_LOCAL_RUNTIME = _register_remediation(
    Action("install_local_runtime", "Set up secure execution on this device", ActionKind.REMEDIATION),
    LEADS_TO_PROTECTED)
CONNECT_OWN_SERVER = _register_remediation(
    Action("connect_own_server", "Connect your own server", ActionKind.REMEDIATION), LEADS_TO_PROTECTED)
RETRY_WHEN_ONLINE = _register_remediation(
    Action("retry_when_online", "Try again when you are back online", ActionKind.REMEDIATION),
    LEADS_TO_PROTECTED)
CONTACT_SUPPORT = _register_remediation(
    Action("contact_support", "Get help", ActionKind.REMEDIATION), LEADS_TO_STOPPING)
ABANDON_SAFELY = _register_remediation(
    Action("abandon_safely", "Stop and keep nothing", ActionKind.REMEDIATION), LEADS_TO_STOPPING)
LEARN_MORE = Action("learn_more", "What does this mean?", ActionKind.INFORMATIONAL)
SHOW_DETAILS = Action("show_details", "Show technical details", ActionKind.INFORMATIONAL)


def _require_a_way_out(actions: Sequence[Action], what: str) -> tuple[Action, ...]:
    for a in actions:
        if a.kind is ActionKind.REMEDIATION and _REMEDIATION_CATALOGUE.get(a.id) is not a:
            raise ValueError(
                f"{what} carries an action labelled 'remediation' that is not a registered one "
                f"({a.id!r}). A label is not a way out; register the action the product implements."
            )
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

    def __init_subclass__(cls, **kw):  # noqa: D105
        # EM3A-IMPL-0003 / F-A1: __post_init__ is virtual, so a subclass could override it and
        # construct a dead-end refusal. There is no legitimate reason to subclass this type, and
        # forbidding it removes the only route around the check.
        raise TypeError(
            "Refusal is final: subclassing it would allow overriding the check that a refusal "
            "always carries an executable way out."
        )

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
    safe_exit: Action = field(default=None)  # set in __post_init__ to the registered exit
    escalation: Action = CONTACT_SUPPORT

    def __init_subclass__(cls, **kw):  # noqa: D105
        raise TypeError("Blocked is final, for the same reason Refusal is.")

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("a blocked state must carry a plain-language reason")
        if self.safe_exit is None:
            object.__setattr__(self, "safe_exit", ABANDON_SAFELY)
        _require_a_way_out((self.safe_exit,), "a blocked state's exit")
        _require_a_way_out((self.escalation,), "a blocked state's escalation")


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
        if type(scope) is not Scope:
            raise TypeError(
                f"policy scopes must come from the closed Scope vocabulary, not {type(scope).__name__}; "
                "an arbitrary orderable key could insert itself anywhere in the precedence order"
            )
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
# The single selection gate
# --------------------------------------------------------------------------------------

class Placement(str, Enum):
    LOCAL = "local"                  # a container on this device
    SELF_HOSTED = "self_hosted"      # the customer's own gateway
    MANAGED = "managed"              # the AgentNode Sandbox

    # There is deliberately NO host value. Three earlier designs modelled host execution as a
    # placement behind a capability token, and three reviews took each of them apart, because inside
    # one Python process there is no boundary against that same process: module state is mutable,
    # underscore names are reachable, any callable returns True. Removing the value is stronger than
    # guarding it — nothing can select what does not exist. The old sandbox.host_trust_policy setting
    # lives on OUTSIDE this contract as an outdated compatibility feature, and 0.24.0 behaviour is
    # unchanged; a future controlled host execution would need a real process and privilege boundary
    # with an authenticated channel, which is not this.


@dataclass(frozen=True)
class Selection:
    placement: Placement
    human_name: str
    consent_required: bool = False   # a remote placement never proceeds on a stale consent

    def __init_subclass__(cls, **kw):  # noqa: D105
        raise TypeError("Selection is final: a subclass could bypass the placement check below.")

    def __post_init__(self) -> None:
        # EM3A-IMPL-0005 / F-A2: a type annotation is documentation, not a runtime check, so
        # Selection("host", ...) was constructible. The value must be a real Placement member.
        if not isinstance(self.placement, Placement):
            raise TypeError(
                f"a selection's placement must be one of {[p.value for p in Placement]}; "
                f"got {self.placement!r}. There is no host placement in this contract."
            )


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


def select_backend(env: Environment) -> Selection | Refusal:
    """The one authoritative gate. Every path — automatic, onboarding, fallback, retry — comes here.

    Returns a :class:`Selection` naming one of the three protected placements, or a
    :class:`Refusal` that always carries a way out. It never returns ``None``, and it cannot return
    host execution because :class:`Placement` has no such value.
    """
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


# The catalogue is complete. Sealing it here is what makes a remediation a way out the product
# implements, rather than a label a caller can mint: after import there is no way to add one, and
# `Refusal` accepts nothing outside it.
_CATALOGUE_SEALED = True
