"""An activation record anyone can check offline (local-worker slice 2, Codex Q-LW2-0001 D13).

A dial-back target becomes `active` only through an observation record the operator commits into the signed,
hash-linked target register (`targets.py`). The record carries no signature of its own -- the register generation
that holds it is signed -- so exporting "the record" alone would export nothing anybody could verify. The export
is therefore the exact signed generations, 1 .. N, of the register:

    {"schema": "agentnode.activation-export/1", "target_id", "generations": [<generation 1>, ..., <generation N>]}

and the verifier believes nothing the file says about itself. It is given, separately, the operator's public key and
a TRUSTED HEAD (generation and digest, read independently of the file -- e.g. from the stand's `head.json` over a
separate channel), and it checks: every generation's canonical bytes and Ed25519 signature, numbering from 1, every
previous-digest link and the register's own successor rules, that the chain ends EXACTLY at the trusted head (a
truncated or a self-declared head is refused), the target's binding (customer, reverse, owner, device, instance,
identity), the transition into `active`, the record digest, the observation's freshness, that the activation is
still the current one at the head, and that the runtime VERSION is kept as the worker's claim (`reported_platform`,
`runtime_version_verified: false`) apart from what the jobs observed. It prints the observations and the claim in
two separately labelled blocks.

    python -m agentnode_sdk.remote_mcp.activation_export export --stand S --target T --out F
    python -m agentnode_sdk.remote_mcp.activation_export verify F --operator-pub HEX --trusted-head G:DIGEST

Refusals exit 2 with a named code: activation_signature_invalid, activation_chain_invalid, activation_head_untrusted,
activation_claim_confused, activation_not_active, activation_binding_invalid, activation_record_invalid,
activation_expired, activation_export_invalid.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.signing_key import compute_key_id

SCHEMA = "agentnode.activation-export/1"
#: What the jobs saw (and the probe derived from it), as opposed to what the worker said about itself.
OBSERVED_KEYS = ("network_none_observed", "memory_ceiling_mb", "storage_mb", "wall_clock_enforced",
                 "cpu_quota_enforced", "isolation_observed", "platform")
CLAIM_KEY = "reported_platform"


class ActivationRefused(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__("%s: %s" % (code, detail))
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------- export
def export(stand, target_id: str) -> dict:
    """The register's generations 1..N exactly as written (`history/<g>.json`), read without any key. The exporter
    makes no claim of its own; the reader verifies everything against a head it trusts for its own reasons."""
    history = Path(stand) / "targets" / "history"
    gens = []
    g = 1
    while (history / ("%d.json" % g)).exists():
        gens.append(json.loads((history / ("%d.json" % g)).read_bytes().decode("utf-8")))
        g += 1
    if not gens or target_id not in gens[-1]["targets"]:
        raise ActivationRefused("activation_export_invalid", "the register has no target %s" % target_id)
    return {"schema": SCHEMA, "target_id": target_id, "generations": gens}


# ---------------------------------------------------------------------- verify
def _parse_head(text: str) -> tuple[int, str]:
    try:
        g, d = str(text).split(":", 1)
        if not g.isdigit() or len(d) != 64 or set(d) - set("0123456789abcdef"):
            raise ValueError
        return int(g), d
    except ValueError:
        raise ActivationRefused("activation_head_untrusted", "a trusted head is <generation>:<64 hex digest>") from None


def verify(bundle: dict, operator_public: bytes, trusted_head: str, *, now: float | None = None) -> dict:
    """Everything above, or ActivationRefused. Returns {"target", "activated_in", "observed", "claim", ...}."""
    head_generation, head_digest = _parse_head(trusted_head)
    if not isinstance(bundle, dict) or set(bundle) != {"schema", "target_id", "generations"} \
            or bundle["schema"] != SCHEMA or not isinstance(bundle["generations"], list) or not bundle["generations"]:
        raise ActivationRefused("activation_export_invalid", "not an %s document" % SCHEMA)
    target_id = bundle["target_id"]
    key = Ed25519PublicKey.from_public_bytes(operator_public)
    key_id = compute_key_id(operator_public)
    prev = None
    for i, doc in enumerate(bundle["generations"], start=1):
        errs = T.validate_document(doc)
        if errs:
            raise ActivationRefused("activation_chain_invalid", "generation %d: %s" % (i, "; ".join(errs[:2])))
        if doc["signature"]["key_id"] != key_id:
            raise ActivationRefused("activation_signature_invalid", "generation %d is signed by another key" % i)
        try:
            key.verify(bytes.fromhex(doc["signature"]["signature"]), T._signed_bytes(doc))
        except (InvalidSignature, ValueError):
            raise ActivationRefused("activation_signature_invalid", "generation %d does not verify" % i) from None
        if doc["generation"] != i:
            raise ActivationRefused("activation_chain_invalid", "position %d holds generation %s" % (i, doc["generation"]))
        if prev is None:
            if doc["prev_digest"] != T.GENESIS:
                raise ActivationRefused("activation_chain_invalid", "generation 1 does not start the chain")
        else:
            errs = T.check_successor(prev, doc)
            if errs:
                raise ActivationRefused("activation_chain_invalid", "at %d: %s" % (i, "; ".join(errs[:2])))
        prev = doc
    last = bundle["generations"][-1]
    if last["generation"] != head_generation or T.digest(last) != head_digest:
        raise ActivationRefused("activation_head_untrusted",
                                "the chain ends at %d:%s, not at the trusted head" % (last["generation"],
                                                                                      T.digest(last)[:16]))
    # ---- the target, as the head has it now
    e = last["targets"].get(target_id)
    if e is None:
        raise ActivationRefused("activation_binding_invalid", "the head has no target %s" % target_id)
    target = T.Target.from_doc(e)
    when = float(now if now is not None else time.time())
    if target.effective_status(when) != T.ACTIVE:
        raise ActivationRefused("activation_not_active", "%s is %s at the trusted head"
                                % (target_id, target.effective_status(when)))
    w = target.worker
    if target.type != T.CUSTOMER or not target.is_reverse or not target.owner_account or not target.device_id \
            or w["identity_uri"] != "agentnode://%s/worker/%s" % (w["deployment"], w["instance"]):
        raise ActivationRefused("activation_binding_invalid", "not a customer dial-back target bound to an owner, a "
                                                              "device and its own identity")
    a = target.attestation or {}
    body = {k: a[k] for k in sorted(set(a) - {"record_digest"})}
    if a.get("record_digest") != T.digest(body) or a.get("identity_uri") != target.identity_uri \
            or a.get("endpoint_digest") != target.endpoint_digest or a.get("assurance") != T.CUSTOMER_OBSERVED:
        raise ActivationRefused("activation_record_invalid", "the observation record does not match its digest, "
                                                             "identity, endpoint or class")
    if not (float(a["measured_at"]) <= when < float(a["expires_at"])):
        raise ActivationRefused("activation_expired", "the observation is valid %s .. %s"
                                % (a["measured_at"], a["expires_at"]))
    # ---- where it became active: the latest transition into active, with THIS record in force since
    activated_in = None
    for doc in bundle["generations"]:
        cur = doc["targets"].get(target_id)
        if cur is None:
            continue
        if cur["status"] == T.ACTIVE and (activated_in is None or prior_status != T.ACTIVE):
            activated_in = doc["generation"]
        prior_status = cur["status"]
    if activated_in is None:
        raise ActivationRefused("activation_not_active", "no generation moves %s into active" % target_id)
    # ---- observations and the claim, kept apart
    props = a.get("properties") or {}
    claim = props.get(CLAIM_KEY)
    observed = {k: props.get(k) for k in OBSERVED_KEYS}
    platform = observed.get("platform") or {}
    from agentnode_sdk.remote_mcp.target_probe import claimed_runtime, reverse_platform_failures
    if not isinstance(claim, dict) or claim.get("runtime_version_verified") is not False \
            or claim.get("runtime_version_source") != "worker_claim" \
            or claimed_runtime(claim.get("runtime_version_raw")) != {k: claim.get(k) for k in claimed_runtime("")} \
            or any(k in platform for k in ("runtime_version", "runtime_version_raw", "runtime_version_source",
                                           "runtime_version_verified")) \
            or reverse_platform_failures(props):
        raise ActivationRefused("activation_claim_confused", "the runtime version must be the worker's unverified "
                                                             "claim, re-parsed from its raw text, of the observed family "
                                                             "and >= 5.8.2, and nowhere among the observations")
    return {"target_id": target_id, "owner_account": target.owner_account, "device_id": target.device_id,
            "identity_uri": target.identity_uri, "activated_in_generation": activated_in,
            "head": {"generation": head_generation, "digest": head_digest},
            "record_digest": a["record_digest"], "measured_at": a["measured_at"], "expires_at": a["expires_at"],
            "observed": observed, "claim": claim}


def render(result: dict) -> str:
    return "\n".join([
        "ACTIVATION VERIFIED  target %s  owner %s  device %s" % (result["target_id"], result["owner_account"],
                                                                result["device_id"]),
        "  identity %s" % result["identity_uri"],
        "  active since generation %d; trusted head %d:%s" % (result["activated_in_generation"],
                                                              result["head"]["generation"], result["head"]["digest"]),
        "  observation record %s, measured %s, valid until %s" % (result["record_digest"], result["measured_at"],
                                                                 result["expires_at"]),
        "OBSERVED (inside isolated jobs, operator-signed):",
        json.dumps(result["observed"], indent=2, sort_keys=True),
        "CLAIM (the worker's own report, NOT verified; it can only refuse):",
        json.dumps(result["claim"], indent=2, sort_keys=True)])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="agentnode-activation")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("export")
    s.add_argument("--stand", required=True)
    s.add_argument("--target", required=True)
    s.add_argument("--out", required=True)
    s = sub.add_parser("verify")
    s.add_argument("file")
    s.add_argument("--operator-pub", required=True, help="the operator's Ed25519 public key, 64 hex")
    s.add_argument("--trusted-head", required=True, help="<generation>:<digest>, obtained independently")
    args = p.parse_args(argv)
    try:
        if args.cmd == "export":
            data = (json.dumps(export(args.stand, args.target), sort_keys=True, indent=1) + "\n").encode("utf-8")
            Path(args.out).write_bytes(data)
            import hashlib
            print("exported %s (%d bytes, sha256 %s)" % (args.out, len(data), hashlib.sha256(data).hexdigest()))
            return 0
        try:
            bundle = json.loads(Path(args.file).read_bytes().decode("utf-8"))
            pub = bytes.fromhex(args.operator_pub.strip())
        except (OSError, ValueError) as exc:
            raise ActivationRefused("activation_export_invalid", type(exc).__name__) from None
        print(render(verify(bundle, pub, args.trusted_head)))
        return 0
    except ActivationRefused as refused:
        print("REFUSED %s: %s" % (refused.code, refused.detail))
        return 2


if __name__ == "__main__":
    sys.exit(main())
