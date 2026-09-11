"""The operator's policy, in the one form its digest is taken over.

`EM3C-EXTERNAL-0017` found that readiness said nothing about the policy it was measured under. A
report taken while the gateway allowed no network at all was accepted as evidence that the gateway
was ready after the operator opened an allowlist -- two different enforcement modes, one
measurement, and nothing in between them to notice. `EM3C-Y6-DECISION-0001` settled the mechanism:
the report carries the digest of the policy it was taken for, and readiness holds only while the
two agree.

This module is the digest's input, and nothing else. It is deliberately separate from
`policy_paths.policy_shape`, which serves the per-job request and effective digests: those two
shapes answer different questions, and folding them together would mean a change made for a job
silently moved every operator's digest (`D1-c`).

## What "canonical" has to mean here

A digest is only a boundary if two inputs that differ in any way that matters cannot produce the
same bytes, and two that differ in no way that matters cannot produce different ones. So:

* keys are sorted, separators fixed, and the encoding is UTF-8 -- key order and whitespace in the
  file on disk have no effect;
* destinations are lower-cased, IDNA-normalised, sorted and de-duplicated, so ``["B.example",
  "a.example"]`` and ``["a.example", "b.example"]`` are the same policy and hash the same;
* integral limits are integers, never ``2.0``, so a float that happens to be whole cannot produce
  a second digest for one policy;
* the required-property set is *inside* the envelope, because "which properties must have been
  measured" is part of what the operator decided, not a fact derived later from it.

## Why the parsing is strict rather than forgiving

`json.loads` keeps the last of a repeated key and says nothing. A config containing
``"egress_allowed"`` twice therefore parses cleanly today, and whichever value loses is invisible.
An unknown key is worse: it reads as a setting that was configured and is not honoured. Both are
refused here, at every depth, before any object is constructed -- and the envelope that gets
digested is rebuilt from the validated values rather than from the parsed text, so a file that got
past a reader could still not smuggle a field into the hash.

Nothing in this module reads or writes the filesystem. It turns text into a validated envelope, or
raises.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

#: Bumped when the meaning of a field changes. It is inside the digest, so a gateway that
#: understands version 2 cannot accept a version 1 report as evidence for a version 2 policy.
SCHEMA_VERSION = 1

#: The three network modes an operator policy can express.
NONE, RESTRICTED, UNRESTRICTED = "none", "restricted", "unrestricted"

#: Properties every policy requires, whatever else it says.
COMMON_REQUIRED = ("container_isolation", "memory_ceiling_enforceable", "verified_cleanup")

#: Limits the operator may set, and the type each is normalised to.
LIMIT_FIELDS: dict[str, type] = {
    "cpu": float,
    "memory_mb": int,
    "processes": int,
    "wall_clock_s": int,
    "disk_mb": int,
}

#: A limit that has a conformance property of its own. `D2`: a configured limit whose enforcement
#: the suite can measure must be measured, or the policy is not backed by anything.
LIMIT_PROPERTIES: dict[str, str] = {
    "memory_mb": "memory_ceiling_enforceable",
}

_ALLOWED_TOP = {"schema_version", "network", "limits", "runtime", "required_properties"}
_ALLOWED_NETWORK = {"mode", "allowed_destinations"}
_ALLOWED_RUNTIME = {"backend", "image_digest", "min_version"}

#: A bare hostname. The same shape the egress allowlist can actually enforce -- no scheme, no
#: port, no path, no wildcard -- checked here so a value that could never be enforced cannot be
#: digested as though it had been.
_HOSTNAME = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))*$")


class OperatorPolicyError(ValueError):
    """The policy cannot be represented canonically, so it cannot be relied on."""


# --------------------------------------------------------------------------- strict parsing


def _no_duplicates(pairs):
    """Object hook that refuses a repeated key instead of silently keeping the last one."""
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise OperatorPolicyError(
                f"the key {key!r} appears more than once. One of the two values would have been "
                "used and the other silently dropped, so neither is trusted.")
        seen[key] = value
    return seen


def loads_strict(text: str) -> dict:
    """Parse JSON, refusing a duplicated key at any depth."""
    try:
        loaded = json.loads(text, object_pairs_hook=_no_duplicates)
    except OperatorPolicyError:
        raise
    except ValueError as exc:
        raise OperatorPolicyError(f"this is not readable as JSON: {exc}") from None
    if not isinstance(loaded, dict):
        raise OperatorPolicyError("the policy has to be an object.")
    return loaded


def _closed(where: str, given: dict, allowed: set) -> None:
    unknown = sorted(set(given) - allowed)
    if unknown:
        raise OperatorPolicyError(
            f"{where} carries {', '.join(repr(u) for u in unknown)}, which this version does not "
            "understand. An unrecognised setting reads as configured and is not honoured, so it "
            "is refused rather than ignored.")


def _number(where: str, value: Any, kind: type):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OperatorPolicyError(f"{where} has to be a number, not {type(value).__name__}.")
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise OperatorPolicyError(f"{where} is not a finite number, so it has no canonical form.")
    if kind is int:
        if isinstance(value, float) and value != int(value):
            raise OperatorPolicyError(f"{where} has to be a whole number.")
        value = int(value)
        if value <= 0:
            raise OperatorPolicyError(f"{where} has to be greater than zero.")
        return value
    value = float(value)
    if value <= 0:
        raise OperatorPolicyError(f"{where} has to be greater than zero.")
    # A float whose value is integral is written as an integer so one policy has one digest.
    return int(value) if value == int(value) else value


def _destination(raw: Any) -> str:
    if not isinstance(raw, str):
        raise OperatorPolicyError(
            f"a destination has to be a hostname, not {type(raw).__name__}.")
    host = raw.strip().lower()
    if not host:
        raise OperatorPolicyError("a destination cannot be empty.")
    try:
        host = host.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        raise OperatorPolicyError(
            f"{raw!r} has no single normalised form, so two spellings of it could hash "
            "differently.") from None
    if not _HOSTNAME.match(host):
        raise OperatorPolicyError(
            f"{raw!r} is not a bare hostname. No scheme, port, path or wildcard -- those cannot "
            "be enforced as an allowlist, so they are refused rather than stored.")
    return host


# --------------------------------------------------------------------------- the envelope


@dataclass(frozen=True)
class OperatorPolicyEnvelope:
    """One operator policy, in the exact form its digest is taken over."""

    schema_version: int
    mode: str
    allowed_destinations: tuple[str, ...]
    limits: tuple[tuple[str, Any], ...]
    runtime: tuple[tuple[str, str], ...]
    required_properties: tuple[str, ...]

    def as_canonical(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "network": {
                "mode": self.mode,
                "allowed_destinations": list(self.allowed_destinations),
            },
            "limits": dict(self.limits),
            "runtime": dict(self.runtime),
            "required_properties": list(self.required_properties),
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(self.as_canonical(), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def enables_network(self) -> bool:
        return self.mode != NONE


def required_for(mode: str, limits: dict) -> tuple[str, ...]:
    """The properties a policy in this mode must have had measured (`D2`).

    A mode's requirement is never satisfied by another mode's: an allowlist policy requires
    `egress_allowlist` and is NOT let through by `network_none`, which is what a report taken
    under a closed policy would carry. That is the whole point of the rule.
    """
    needed = set(COMMON_REQUIRED)
    if mode == NONE:
        needed.add("network_none")
    elif mode == RESTRICTED:
        needed.add("egress_allowlist")
    elif mode == UNRESTRICTED:
        needed.add("network_unrestricted")
    for field_name, property_name in LIMIT_PROPERTIES.items():
        if field_name in limits:
            needed.add(property_name)
    return tuple(sorted(needed))


def build(mode: str, destinations=(), limits=None, runtime=None) -> OperatorPolicyEnvelope:
    """Validate and normalise into the envelope. Everything digested comes through here."""
    if mode not in (NONE, RESTRICTED, UNRESTRICTED):
        raise OperatorPolicyError(
            f"{mode!r} is not a network mode. It is one of {NONE!r}, {RESTRICTED!r} or "
            f"{UNRESTRICTED!r}.")

    hosts = tuple(sorted({_destination(d) for d in (destinations or ())}))
    if mode == RESTRICTED and not hosts:
        raise OperatorPolicyError(
            "a restricted policy names no destination, so there is nothing to allow. Name the "
            "hosts, or set no network at all.")
    if mode != RESTRICTED and hosts:
        raise OperatorPolicyError(
            f"a {mode!r} policy cannot carry destinations: they would not be what decides.")

    clean_limits: dict[str, Any] = {}
    for name, raw in dict(limits or {}).items():
        if name not in LIMIT_FIELDS:
            raise OperatorPolicyError(
                f"{name!r} is not a limit this version understands, so it would be configured "
                "and not honoured.")
        clean_limits[name] = _number(f"limits.{name}", raw, LIMIT_FIELDS[name])

    clean_runtime: dict[str, str] = {}
    for name, raw in dict(runtime or {}).items():
        if name not in _ALLOWED_RUNTIME:
            raise OperatorPolicyError(f"{name!r} is not a runtime requirement this version knows.")
        if not isinstance(raw, str) or not raw.strip():
            raise OperatorPolicyError(f"runtime.{name} has to be a non-empty string.")
        clean_runtime[name] = raw.strip()

    return OperatorPolicyEnvelope(
        schema_version=SCHEMA_VERSION,
        mode=mode,
        allowed_destinations=hosts,
        limits=tuple(sorted(clean_limits.items())),
        runtime=tuple(sorted(clean_runtime.items())),
        required_properties=required_for(mode, clean_limits),
    )


def from_config(config: dict) -> OperatorPolicyEnvelope:
    """The envelope implied by a gateway config document.

    A config that says nothing about egress is the closed default, which is a real policy with a
    real digest rather than an absence -- so "the operator has set nothing" and "the operator set
    no network" are the same state and cannot drift apart.
    """
    allowed = config.get("egress_allowed")
    if allowed is None:
        return build(NONE)
    if not isinstance(allowed, list):
        raise OperatorPolicyError("egress_allowed has to be a list of hostnames.")
    if not allowed:
        return build(NONE)
    return build(RESTRICTED, allowed,
                 limits=config.get("egress_limits") or None,
                 runtime=config.get("runtime_requirements") or None)


def from_document(text: str) -> OperatorPolicyEnvelope:
    """Parse a stored canonical envelope back, strictly, and rebuild it from validated values."""
    raw = loads_strict(text)
    _closed("the policy", raw, _ALLOWED_TOP)
    for name in _ALLOWED_TOP:
        if name not in raw:
            raise OperatorPolicyError(
                f"the policy has no {name!r}. A missing field is not a default here: it would "
                "mean the digest was taken over something other than what is being read.")

    if raw["schema_version"] != SCHEMA_VERSION:
        raise OperatorPolicyError(
            f"this policy is schema version {raw['schema_version']!r} and this gateway speaks "
            f"{SCHEMA_VERSION}. It is not read rather than read approximately.")

    network = raw["network"]
    if not isinstance(network, dict):
        raise OperatorPolicyError("network has to be an object.")
    _closed("network", network, _ALLOWED_NETWORK)
    for name in _ALLOWED_NETWORK:
        if name not in network:
            raise OperatorPolicyError(f"network has no {name!r}.")

    limits = raw["limits"]
    runtime = raw["runtime"]
    if not isinstance(limits, dict) or not isinstance(runtime, dict):
        raise OperatorPolicyError("limits and runtime have to be objects.")

    envelope = build(network["mode"], network["allowed_destinations"], limits, runtime)

    # The stored required set is not trusted as an input: it is recomputed and compared. A file
    # that listed fewer properties than its own mode demands would otherwise lower the bar for
    # itself, which is precisely the move this whole mechanism exists to prevent.
    stated = raw["required_properties"]
    if not isinstance(stated, list) or sorted(map(str, stated)) != list(envelope.required_properties):
        raise OperatorPolicyError(
            "the required properties recorded in this policy are not the ones its own mode and "
            "limits demand. It is refused rather than reconciled.")
    return envelope
