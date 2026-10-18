"""Shared builders for the target-router tests: a signed register with one managed target, and
customer target specs. Real Ed25519 operator key, real files, real lock."""
from __future__ import annotations

import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentnode_sdk.remote_mcp import targets as T

DEPLOYMENT = "rmcp-test"


def raw_pub(k):
    return k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def worker(instance: str, address: str = "tcps://10.0.0.2:8443", *, gateway: str = "g1") -> dict:
    return {"address": address, "deployment": DEPLOYMENT, "instance": instance,
            "identity_uri": "agentnode://%s/worker/%s" % (DEPLOYMENT, instance),
            "topology": "separate-worker-host", "protocols": ["agentnode-worker/1"],
            "pair": {"gateway": gateway, "worker": instance}, "key_slot": "k1", "config_digest": ""}


def attestation(t_type: str, w: dict, *, now: float | None = None, seconds: float | None = None,
                properties: dict | None = None) -> dict:
    now = time.time() if now is None else now
    assurance = T.ASSURANCE_OF_TYPE[t_type]
    seconds = seconds if seconds is not None else T.ATTESTATION_MAX_SECONDS[assurance] - 60
    body = {"assurance": assurance, "suite": "agentnode-target-probe/1", "measured_at": now - 1,
            "expires_at": now - 1 + seconds, "identity_uri": w["identity_uri"],
            "endpoint_digest": T.endpoint_digest(w),
            "properties": properties or {"network_none_observed": True, "memory_ceiling_mb": 1024,
                                         "wall_clock_s": 120, "storage_mb": 512},
            "results": {"egress": "no connection observed", "memory": "killed", "wall_clock": "killed"},
            "challenge_nonces": ["n-%d" % int(now)]}
    body["record_digest"] = T.digest({k: body[k] for k in sorted(body)})
    return body


def managed_spec(instance: str = "w1", *, now: float | None = None) -> dict:
    w = worker(instance)
    return {"target_id": "managed-" + instance, "type": T.MANAGED, "owner_account": None,
            "created_at": (now or time.time()) - 10, "status": T.ACTIVE, "expires_at": None, "worker": w,
            "limits": {"concurrency": 4, "queue": 8}, "attestation": attestation(T.MANAGED, w, now=now)}


def customer_spec(target_id: str, owner: str, instance: str, *, port: int = 8444, status: str = T.INVITED,
                  expires_at: float | None = None) -> dict:
    w = worker(instance, "tcps://10.0.0.2:%d" % port)
    return {"target_id": target_id, "type": T.CUSTOMER, "owner_account": owner, "created_at": 0.0,
            "status": status, "expires_at": expires_at, "worker": w,
            "limits": {"concurrency": 1, "queue": 2}, "attestation": None}


class Clock:
    def __init__(self, t: float | None = None):
        self.t = time.time() if t is None else t

    def __call__(self) -> float:
        return self.t


def make_register(stand, *, clock=None, on_change=None):
    op = Ed25519PrivateKey.generate()
    (stand / "catalog").mkdir(parents=True, exist_ok=True)
    reg = T.Register(stand, raw_pub(op), operator_private=op, clock=clock or time.time, on_change=on_change)
    reg.init(managed_spec(), actor="t")
    return reg, op


def activate(reg: T.Register, target_id: str, *, now: float | None = None) -> None:
    """invited -> enrolled -> probing -> (attested) -> active, as the lifecycle does."""
    snap = reg.load()
    t = snap.target(target_id)
    for st in (T.ENROLLED, T.PROBING):
        reg.set_status(target_id, st, actor="t")
    reg.set_attestation(target_id, attestation(T.CUSTOMER, t.worker, now=now), actor="t")
    reg.set_status(target_id, T.ACTIVE, actor="t")
