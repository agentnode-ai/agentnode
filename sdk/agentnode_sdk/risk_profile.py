"""Usage risk profile for installed packages.

Computes a risk score from static signals (permissions, trust, credentials)
and runtime signals (audit deny rate, injection markers). Separate from
verification score — risk answers "how risky is the usage?" not "does it
work reliably?"

The score is informational only. It never blocks execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RiskProfile:
    risk_level: str          # "low", "medium", "high"
    risk_score: int          # 0-100 (higher = riskier)
    signals: list[str]       # human-readable signal descriptions
    risk_flags: list[str] = field(default_factory=list)  # semantic flags, not scored
    backend_hint: dict | None = None  # optional backend-provided risk data, displayed only


def compute_risk_profile(slug: str, entry: dict) -> RiskProfile:
    """Compute risk profile from lockfile entry + local audit history."""
    scored = _static_signals(entry) + _runtime_signals(slug)
    score = min(100, sum(pts for pts, _ in scored))
    signals = [desc for _, desc in scored]

    if score <= 20:
        level = "low"
    elif score <= 45:
        level = "medium"
    else:
        level = "high"

    return RiskProfile(
        risk_level=level,
        risk_score=score,
        signals=signals,
        risk_flags=_compute_flags(entry),
        backend_hint=_backend_hint(entry),
    )


def _static_signals(entry: dict) -> list[tuple[int, str]]:
    """Score from permissions, trust, credentials."""
    signals: list[tuple[int, str]] = []
    perms = entry.get("permissions") or {}

    net = perms.get("network_level", "none")
    if net in ("external", "full"):
        signals.append((25, "Uses external network access"))
    elif net == "internal":
        signals.append((10, "Uses internal network access"))

    fs = perms.get("filesystem_level", "none")
    if fs == "write":
        signals.append((20, "Can write to filesystem"))
    elif fs == "read":
        signals.append((5, "Reads from filesystem"))

    code = perms.get("code_execution_level", "none")
    if code != "none":
        signals.append((20, f"Executes code ({code})"))

    connector = entry.get("connector")
    if connector and isinstance(connector, dict):
        auth_type = connector.get("auth_type")
        if auth_type:
            signals.append((15, f"Requires credentials ({auth_type})"))

    trust = entry.get("trust_level", "unknown")
    if trust == "unverified":
        signals.append((15, "Trust level: unverified"))
    elif trust == "verified":
        signals.append((5, "Trust level: verified"))
    # "preview", "trusted", "curated" → 0 points (no signal)

    return signals


def _runtime_signals(slug: str) -> list[tuple[int, str]]:
    """Score from local audit history."""
    signals: list[tuple[int, str]] = []

    try:
        from agentnode_sdk.cli.audit import read_audit_entries

        total = 0
        denies = 0
        for e in read_audit_entries():
            if e.get("slug") != slug:
                continue
            total += 1
            if (e.get("action") or "").lower() == "deny":
                denies += 1

        if total >= 5 and denies / total > 0.1:
            signals.append((10, f"High deny rate ({denies}/{total} runs denied)"))
    except Exception:
        pass

    return signals


_EXTERNAL_WRITE_CAPABILITIES = frozenset({
    "email_sending",
    "calendar_management",
    "task_management",
})


def _compute_flags(entry: dict) -> list[str]:
    """Derive semantic risk flags. These do not affect the score."""
    flags: list[str] = []
    perms = entry.get("permissions") or {}
    net = perms.get("network_level", "none")
    has_external_net = net in ("external", "full")

    connector = entry.get("connector")
    has_connector = bool(connector and isinstance(connector, dict) and connector.get("auth_type"))

    caps = set(entry.get("capability_ids", []))
    has_write_cap = bool(caps & _EXTERNAL_WRITE_CAPABILITIES)

    if has_external_net and (has_connector or has_write_cap):
        flags.append("external_write_capable")

    return flags


def get_risk_profile(slug: str) -> RiskProfile | None:
    """Return the risk profile for an installed package, or None if not installed."""
    from agentnode_sdk.installer import read_lockfile

    lock = read_lockfile()
    entry = lock.get("packages", {}).get(slug)
    if entry is None:
        return None
    return compute_risk_profile(slug, entry)


def _backend_hint(entry: dict) -> dict | None:
    """Read optional backend risk data. Displayed separately, not scored."""
    hint = entry.get("risk_score") or entry.get("risk_profile")
    if hint is None:
        return None
    if isinstance(hint, (int, float)):
        return {"score": hint}
    if isinstance(hint, dict):
        return {
            k: v for k, v in hint.items()
            if k in ("score", "level", "signals")
        }
    return None
