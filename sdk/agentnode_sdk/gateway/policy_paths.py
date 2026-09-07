"""The closed vocabulary of policy fields a job may pin, and what "narrowed" means for each.

`EM3C-DIGEST-DECISION-0001` chose **A1**: a request carries two disjoint lists of policy *paths*,
one mandatory and one optional. Its named risk is the reason this module exists rather than a
dict literal somewhere: A1 is only safe if an unknown, duplicated or overlapping path **fails
closed**. A path nobody validated is a requirement that silently is not one, and a typo would then
read as "satisfied" — the same shape of defect as a check that cannot see its input reporting the
good answer.

So the vocabulary is closed and small, every path has an explicit narrowing rule, and anything
outside it raises.
"""
from __future__ import annotations

from typing import Any

#: Every field a request may pin. Closed on purpose: a path outside this set is refused rather
#: than ignored, because ignoring it would turn a stated requirement into no requirement at all.
POLICY_PATHS: tuple[str, ...] = (
    "network.enabled",
    "network.allowed_destinations",
    "limits.cpu",
    "limits.memory_mb",
    "limits.processes",
    "limits.wall_clock_s",
)


class PolicyPathError(ValueError):
    """A requirement names something this build cannot decide. Always refuse."""


def validate_paths(mandatory, optional) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Both lists, checked together. Fail closed on anything unusable.

    Refused: a path outside the vocabulary, a duplicate within a list, and a path that appears in
    both lists. The last one matters most — a field that is both mandatory and optional has no
    defined behaviour, and picking one silently would decide a security question by accident.
    """
    man = tuple(str(p) for p in (mandatory or ()))
    opt = tuple(str(p) for p in (optional or ()))
    for label, paths in (("mandatory", man), ("optional", opt)):
        unknown = [p for p in paths if p not in POLICY_PATHS]
        if unknown:
            raise PolicyPathError(
                f"{label} names {unknown!r}, which this build does not know how to enforce. "
                f"Known fields: {', '.join(POLICY_PATHS)}"
            )
        if len(set(paths)) != len(paths):
            raise PolicyPathError(f"{label} lists the same field twice")
    both = sorted(set(man) & set(opt))
    if both:
        raise PolicyPathError(
            f"{both!r} is listed as both mandatory and optional; a field cannot be both"
        )
    return man, opt


def policy_shape(policy: Any) -> dict[str, Any]:
    """The B1 canonical shape: exactly the fields the vocabulary can pin, and nothing else.

    `None` for the destination set means *unrestricted* and is deliberately distinct from an empty
    set, which means nothing is reachable. Collapsing them would make the widest and narrowest
    values digest the same.
    """
    net = getattr(policy, "network", None)
    limits = getattr(policy, "limits", None)
    dests = getattr(net, "allowed_destinations", None)
    return {
        "network.enabled": bool(getattr(net, "enabled", False)),
        "network.allowed_destinations": None if dests is None else sorted(dests),
        "limits.cpu": getattr(limits, "cpu", None),
        "limits.memory_mb": getattr(limits, "memory_mb", None),
        "limits.processes": getattr(limits, "processes", None),
        "limits.wall_clock_s": getattr(limits, "wall_clock_s", None),
    }


def _is_narrower(path: str, requested: Any, effective: Any) -> bool:
    """True when `effective` gives strictly less than `requested` for this field."""
    if path == "network.enabled":
        return bool(requested) and not bool(effective)
    if path == "network.allowed_destinations":
        if requested is None:
            return effective is not None          # unrestricted -> a list is narrower
        if effective is None:
            return False                          # a list -> unrestricted would be WIDER
        return set(effective) < set(requested)
    # every remaining path is a numeric ceiling: less is narrower
    if requested is None or effective is None:
        return False
    try:
        return float(effective) < float(requested)
    except (TypeError, ValueError):
        return False


def narrowed_paths(requested_shape: dict, effective_shape: dict) -> tuple[str, ...]:
    """Which pinnable fields the gateway narrowed. Only fields in the closed vocabulary."""
    return tuple(
        p for p in POLICY_PATHS
        if _is_narrower(p, requested_shape.get(p), effective_shape.get(p))
    )


def widened_paths(requested_shape: dict, effective_shape: dict) -> tuple[str, ...]:
    """Which fields the gateway WIDENED -- which must never happen, and is checked rather than
    assumed. Widening is narrowing with the arguments swapped."""
    return tuple(
        p for p in POLICY_PATHS
        if _is_narrower(p, effective_shape.get(p), requested_shape.get(p))
    )


def describe_deltas(paths, requested_shape: dict, effective_shape: dict) -> list[dict]:
    """The deltas a caller sees: which field, what was asked, what was granted."""
    return [
        {
            "field": p,
            "requested": requested_shape.get(p),
            "effective": effective_shape.get(p),
        }
        for p in paths
    ]
