"""What an installed dial-back agent trusts, and the signed `target.json` it is paired with (local-worker slice 2,
Codex Q-LW2-0003 Q4, D7 as amended).

WebPKI tells the agent it reached a server that holds a certificate for `mcp.agentnode.net`; it does not tell the
agent which deployment, which operator or which gateway instance that server speaks for. Those come from the SIGNED
RELEASE MANIFEST the installer verified before anything else ran (`deploy/local-worker/release`), whose channel block
pins them; the installer writes that block, root-owned, as `pins.json`:

    {"schema": "agentnode.worker-pins/1", "channel", "release_seq", "control_url", "anchor_sha256",
     "operator_pub", "gateway_deployment", "gateway_instance"}

Pairing material and every later start are checked against it: the deployment anchor's SHA-256 (canonical DER), the
operator's Ed25519 signature over `target.json`, the control URL, the deployment and the gateway instance. Anything
else is `pair_material_untrusted` -- never "accept and remember". A rotation of any of them needs a newly signed,
non-downgraded release.

`target.json` (written by the pairing helper, signed with the operator key):

    {"schema": "agentnode.worker-target/1", "target_id", "device_id", "deployment", "instance", "accept_gateway",
     "control", "limits": {"concurrency", "queue"}, "issued_at", "signature": {"key_id", "signature"}}

It names no account and carries no credential.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

PINS_SCHEMA = "agentnode.worker-pins/1"
TARGET_SCHEMA = "agentnode.worker-target/1"
TARGET_DOMAIN = b"agentnode.worker-target/1\n"
PIN_KEYS = ("schema", "channel", "release_seq", "control_url", "anchor_sha256", "operator_pub",
            "gateway_deployment", "gateway_instance")
TARGET_KEYS = ("schema", "target_id", "device_id", "deployment", "instance", "accept_gateway", "control", "limits",
               "issued_at", "signature")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class Untrusted(Exception):
    """Material that does not match what the signed release pinned: `pair_material_untrusted`."""

    code = "pair_material_untrusted"

    def __init__(self, detail: str) -> None:
        super().__init__("pair_material_untrusted: " + detail)
        self.detail = detail


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def anchor_sha256(pem_or_der: bytes) -> str:
    """SHA-256 of the canonical DER of the deployment anchor certificate (PEM or DER in)."""
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding

    raw = bytes(pem_or_der)
    cert = x509.load_pem_x509_certificate(raw) if raw.lstrip().startswith(b"-----") else \
        x509.load_der_x509_certificate(raw)
    return hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest()


def read_pins(path) -> dict:
    try:
        pins = json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise Untrusted("the installed pins cannot be read (%s)" % type(exc).__name__) from None
    from agentnode_sdk.pki import identity as _identity
    from agentnode_sdk.worker import edge as _edge
    if not isinstance(pins, dict) or set(pins) != set(PIN_KEYS) or pins["schema"] != PINS_SCHEMA \
            or not _HEX64.match(str(pins["anchor_sha256"])) or not _HEX64.match(str(pins["operator_pub"])) \
            or not _identity.is_component(str(pins["gateway_deployment"])) \
            or not _identity.is_component(str(pins["gateway_instance"])) \
            or not isinstance(pins["release_seq"], int) or isinstance(pins["release_seq"], bool):
        raise Untrusted("the installed pins are malformed")
    try:
        if _edge.control_url(pins["control_url"]).text != pins["control_url"]:
            raise Untrusted("the pinned control URL is not in its canonical form")
    except _edge.EdgeRefused as exc:
        raise Untrusted("the pinned control URL is unusable: " + exc.detail) from None
    return pins


def target_signed_bytes(doc: dict) -> bytes:
    return TARGET_DOMAIN + canonical({k: v for k, v in doc.items() if k != "signature"})


def sign_target(doc: dict, operator_private) -> dict:
    """The pairing helper's side: sign a target document with the operator key."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from agentnode_sdk.signing_key import compute_key_id

    body = {k: doc[k] for k in TARGET_KEYS if k != "signature"}
    pub = operator_private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    body["signature"] = {"key_id": compute_key_id(pub), "signature": operator_private.sign(target_signed_bytes(body)).hex()}
    return body


def check_target(doc: dict, pins: dict) -> dict:
    """`target.json` against the pins: shape, operator signature, control URL, deployment, gateway instance."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from agentnode_sdk.pki import identity as _identity
    from agentnode_sdk.signing_key import compute_key_id

    if not isinstance(doc, dict) or set(doc) != set(TARGET_KEYS) or doc["schema"] != TARGET_SCHEMA:
        raise Untrusted("target.json is not a %s document" % TARGET_SCHEMA)
    pub = bytes.fromhex(pins["operator_pub"])
    sig = doc["signature"] if isinstance(doc["signature"], dict) else {}
    if sig.get("key_id") != compute_key_id(pub):
        raise Untrusted("target.json is signed by another operator key")
    try:
        Ed25519PublicKey.from_public_bytes(pub).verify(bytes.fromhex(str(sig.get("signature"))), target_signed_bytes(doc))
    except (InvalidSignature, ValueError):
        raise Untrusted("target.json's signature does not verify") from None
    if doc["control"] != pins["control_url"]:
        raise Untrusted("target.json names another control endpoint")
    if doc["deployment"] != pins["gateway_deployment"] or doc["accept_gateway"] != pins["gateway_instance"]:
        raise Untrusted("target.json names another deployment or gateway instance")
    if not _identity.is_component(str(doc["instance"])) or not isinstance(doc["limits"], dict) \
            or set(doc["limits"]) != {"concurrency", "queue"}:
        raise Untrusted("target.json is malformed")
    return doc


def check_anchor(pem: bytes, pins: dict) -> None:
    try:
        seen = anchor_sha256(pem)
    except Exception:                                          # noqa: BLE001 - not a certificate
        raise Untrusted("the deployment anchor is not a certificate") from None
    if seen != pins["anchor_sha256"]:
        raise Untrusted("the deployment anchor is not the pinned one")


def check_installed(pins_path, target_path, anchor_path, *, control: str, deployment: str, accept: frozenset) -> dict:
    """Every start of a 443 agent: the pins, the signed target, the anchor on disk and the arguments it was started
    with must all agree. Returns the verified target document."""
    pins = read_pins(pins_path)
    try:
        doc = json.loads(Path(target_path).read_bytes().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise Untrusted("target.json cannot be read (%s)" % type(exc).__name__) from None
    check_target(doc, pins)
    try:
        check_anchor(Path(anchor_path).read_bytes(), pins)
    except OSError as exc:
        raise Untrusted("the deployment anchor cannot be read (%s)" % type(exc).__name__) from None
    from agentnode_sdk.worker import edge as _edge
    try:
        started = _edge.control_url(control).text
    except _edge.EdgeRefused as exc:
        raise Untrusted("the control address is unusable: " + exc.detail) from None
    if started != pins["control_url"]:
        raise Untrusted("started for another control endpoint than the pinned one")
    if deployment != pins["gateway_deployment"] or frozenset(accept) != frozenset({pins["gateway_instance"]}):
        raise Untrusted("started for another deployment or gateway instance than the pinned one")
    return doc


__all__ = ["PINS_SCHEMA", "TARGET_SCHEMA", "Untrusted", "anchor_sha256", "canonical", "check_anchor",
           "check_installed", "check_target", "read_pins", "sign_target", "target_signed_bytes"]
