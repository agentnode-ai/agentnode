"""EM-3C wire protocol: what a client and a self-hosted gateway say to each other.

`EM3C-ARCHITECTURE-0001` chose **T-C + S-B**, and both halves show up here.

**S-B — the client sends the work *and the properties it requires*, and the gateway refuses the
job server-side when it cannot satisfy them.** So a job carries its requirements explicitly, and a
refusal is a normal, structured answer rather than an error. The gateway never silently downgrades
a job to something it *can* do.

**T-C — the gateway is measured, and a report is bound to an identity and a version.** Every
response the gateway sends -- hello, pairing, job submission, status, cancellation, and the
not-found answers -- carries its identity, its version and a fingerprint over both, so a client can
tie any answer to the build that produced it. A gateway that changes either invalidates what the
client knew about it. `GatewayService.stamp` is the single place that adds them, so an endpoint
added later cannot quietly omit them.

What this module is *not*: it is not transport. It defines the bytes that are signed and the rules
for accepting them. HTTP, TLS and framing live elsewhere, and TLS in particular is deliberately not
this module's business — on loopback there is nothing to encrypt, and for a real deployment the
transport is a separate decision that must not be pre-empted by baking it in here.

Three properties the wire format has to carry, because each of them is something an attacker gets
for free otherwise:

* **integrity of what runs.** The artifact is identified by its sha256, and that digest is inside
  the signed envelope. A gateway that receives a different artifact than the one the client signed
  for refuses before anything starts.
* **integrity of what is allowed.** The composed policy is hashed the same way and also signed, so
  a job cannot be replayed against a laxer policy than the one it was authorised under.
* **freshness.** Every request carries a nonce and a timestamp. The gateway remembers nonces for
  the acceptance window and refuses a repeat, so a captured request cannot be replayed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

#: Bumped when the meaning of a field changes. A gateway refuses a version it does not implement
#: rather than guessing, because guessing is how a client ends up believing a property holds.
PROTOCOL_VERSION = "em3c/1"

#: How far apart the two clocks may be before a request is refused as stale. Wide enough for an
#: ordinary skew, narrow enough that a captured request stops being useful quickly.
CLOCK_SKEW_SECONDS = 120

#: Nonces are remembered for at least the acceptance window, so a replay inside the window is
#: caught by the cache and one outside it is caught by the timestamp.
NONCE_TTL_SECONDS = CLOCK_SKEW_SECONDS * 2


class ProtocolError(Exception):
    """The message is not one this build can act on. Always refuse rather than interpret."""


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """The exact bytes that get signed.

    Sorted keys and no incidental whitespace, so two encoders of the same dict produce the same
    signature. Without this the signature would depend on how the sender happened to serialise.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def policy_digest(granted: Any) -> str:
    """A stable digest of the granted policy, so a job cannot be replayed under a laxer one.

    Only the fields that decide what the sandbox may do are included, in a fixed order. A change
    to any of them changes the digest, and a job signed for one policy will not verify against
    another.
    """
    net = getattr(granted, "network", None)
    limits = getattr(granted, "limits", None)
    dests = getattr(net, "allowed_destinations", None)
    shape = {
        "network_enabled": bool(getattr(net, "enabled", False)),
        # None (unrestricted) and a set are different things and must digest differently
        "network_destinations": None if dests is None else sorted(dests),
        "cpu": getattr(limits, "cpu", None),
        "memory_mb": getattr(limits, "memory_mb", None),
        "processes": getattr(limits, "processes", None),
        "wall_clock_s": getattr(limits, "wall_clock_s", None),
    }
    return digest(canonical_bytes(shape))


def new_nonce() -> str:
    return secrets.token_hex(16)


def sign(secret: bytes, payload: dict[str, Any]) -> str:
    return hmac.new(secret, canonical_bytes(payload), hashlib.sha256).hexdigest()


def verify_signature(secret: bytes, payload: dict[str, Any], signature: str) -> bool:
    """Constant-time comparison. A timing-variable check leaks the signature one byte at a time."""
    return hmac.compare_digest(sign(secret, payload), str(signature or ""))


@dataclass(frozen=True)
class JobRequest:
    """One unit of work, and everything the gateway needs to refuse it for the right reason."""

    job_id: str
    run_id: str
    artifact_sha256: str
    policy_sha256: str
    #: What the client REQUIRES of the backend. S-B: the gateway refuses if it cannot satisfy
    #: these, rather than running the job with less.
    required_properties: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    network: str = "none"
    allowed_domains: tuple[str, ...] = ()
    wall_clock_s: int = 60
    nonce: str = field(default_factory=new_nonce)
    issued_at: float = field(default_factory=time.time)

    def to_payload(self) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL_VERSION,
            "job_id": self.job_id,
            "run_id": self.run_id,
            "artifact_sha256": self.artifact_sha256,
            "policy_sha256": self.policy_sha256,
            "required_properties": sorted(self.required_properties),
            "command": list(self.command),
            "network": self.network,
            "allowed_domains": sorted(self.allowed_domains),
            "wall_clock_s": int(self.wall_clock_s),
            "nonce": self.nonce,
            "issued_at": float(self.issued_at),
        }

    @staticmethod
    def from_payload(payload: dict[str, Any]) -> JobRequest:
        if payload.get("protocol") != PROTOCOL_VERSION:
            raise ProtocolError(
                f"this gateway speaks {PROTOCOL_VERSION}, the request says "
                f"{payload.get('protocol')!r}"
            )
        try:
            return JobRequest(
                job_id=str(payload["job_id"]),
                run_id=str(payload["run_id"]),
                artifact_sha256=str(payload["artifact_sha256"]),
                policy_sha256=str(payload["policy_sha256"]),
                required_properties=tuple(payload.get("required_properties") or ()),
                command=tuple(payload.get("command") or ()),
                network=str(payload.get("network", "none")),
                allowed_domains=tuple(payload.get("allowed_domains") or ()),
                wall_clock_s=int(payload.get("wall_clock_s", 60)),
                nonce=str(payload["nonce"]),
                issued_at=float(payload["issued_at"]),
            )
        except KeyError as exc:
            raise ProtocolError(f"the request is missing {exc.args[0]!r}") from exc
        except (TypeError, ValueError) as exc:
            raise ProtocolError(f"the request is malformed: {exc}") from exc


class NonceCache:
    """Remembers nonces for the acceptance window. A repeat inside it is a replay.

    Deliberately not a set that grows forever: entries expire, and a request older than the window
    is refused by its timestamp before the cache is even consulted. The two together mean a
    captured request is useless in both directions -- too old, or already seen.
    """

    def __init__(self, ttl_seconds: int = NONCE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._seen: dict[str, float] = {}

    def _expire(self, now: float) -> None:
        cutoff = now - self._ttl
        for nonce in [n for n, t in self._seen.items() if t < cutoff]:
            del self._seen[nonce]

    def check_and_remember(self, nonce: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._expire(now)
        if nonce in self._seen:
            raise ProtocolError("this request has already been used (replay)")
        self._seen[nonce] = now

    def __len__(self) -> int:                                    # pragma: no cover - diagnostics
        return len(self._seen)


def check_freshness(issued_at: float, now: float | None = None,
                    skew: int = CLOCK_SKEW_SECONDS) -> None:
    """Refuse a request that is too old, and one dated too far in the future.

    The future case matters: without it, a captured request could be given a timestamp far ahead
    and stay valid indefinitely.
    """
    now = time.time() if now is None else now
    delta = now - float(issued_at)
    if delta > skew:
        raise ProtocolError(f"this request is {int(delta)}s old; the limit is {skew}s")
    if delta < -skew:
        raise ProtocolError(
            f"this request is dated {int(-delta)}s in the future; the two clocks disagree"
        )
