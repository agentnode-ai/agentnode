"""Pairing a dial-back worker with an account: `agentnode-worker-pair/1` (local-worker slice 2, Codex Q-LW2-0001 D5,
amended; Q-LW2-0002 Q2; Q-LW2-0003 Q4).

The shape of the OAuth 2.0 device authorization grant -- an agent that cannot take a browser asks for a code, a
person approves it in their browser, the agent collects -- but NOT a claim of RFC 8628 conformance, and the agent
never receives a token of any kind. What it receives, once, is a worker identity and a pair credential:

  1. The agent makes its worker key (P-256, never leaves the machine), a request (CSR) and a transport key (P-256,
     ECDH), and asks `POST /agent/v1/pair/start`. It is told a device code (kept secret by the agent), a short user
     code (shown to the person), where to approve it and how often it may ask.
  2. The person opens `https://mcp.agentnode.net/agent/pair` in their browser, signed in to their account within the
     last 300 s (a fresh joining code otherwise), types the user code, checks the fingerprint the agent printed and
     approves. That creates ONE short-lived, single-use authorization -- nothing the agent could use.
  3. The agent polls `POST /agent/v1/pair/poll`, each poll signed with its worker key. Once the authorization is
     materialized (by the root pairing helper: a worker device in the account, a certificate for the agent's own key,
     a register entry, a pair key) it receives: its certificate, the deployment anchor, the trust lists, the
     operator-signed `target.json` and the pair key SEALED to its transport key (ECDH P-256 -> HKDF-SHA256 ->
     AES-256-GCM). It acknowledges (`POST /agent/v1/pair/ack`); after that the code is `pair_code_consumed`.
     A success that was lost on the way is asked for again with the same device code and key: the same result, never
     a second identity.

Everything received is checked against the SIGNED release's pins before it is written (`pair_material_untrusted`).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from pathlib import Path

PROTOCOL = "agentnode-worker-pair/1"
START_PATH = "/agent/v1/pair/start"
POLL_PATH = "/agent/v1/pair/poll"
ACK_PATH = "/agent/v1/pair/ack"
PAGE_PATH = "/agent/pair"
POLL_DOMAIN = b"agentnode-pair-poll/1\n"
SEAL_INFO = b"agentnode-pair-seal/1"
MAX_BODY = 16384


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def unb64(text: str) -> bytes:
    text = str(text or "")
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if b64(raw) != text:
        raise ValueError("not canonical unpadded base64url")
    return raw


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def code_hash(device_code: str) -> str:
    return sha256_hex(("agentnode-device-code\n" + str(device_code)).encode("ascii", "replace"))


def seal_aad(target_id: str, instance: str, device_code_hash: str) -> bytes:
    return ("%s\n%s\n%s\n%s" % (PROTOCOL, target_id, instance, device_code_hash)).encode("ascii")


# ---------------------------------------------------------------------- the seal
def seal(plaintext: bytes, transport_public_raw: bytes, aad: bytes) -> dict:
    """ECDH(P-256, ephemeral) -> HKDF-SHA256 -> AES-256-GCM. Only the holder of the transport key can open it."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), transport_public_raw)
    eph = ec.generate_private_key(ec.SECP256R1())
    shared = eph.exchange(ec.ECDH(), peer)
    epk = eph.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=epk + transport_public_raw, info=SEAL_INFO).derive(shared)
    nonce = os.urandom(12)
    return {"alg": "ECDH-P256+HKDF-SHA256+A256GCM", "epk": b64(epk), "nonce": b64(nonce),
            "ct": b64(AESGCM(key).encrypt(nonce, plaintext, aad))}


def unseal(sealed: dict, transport_private, aad: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    if not isinstance(sealed, dict) or sealed.get("alg") != "ECDH-P256+HKDF-SHA256+A256GCM":
        raise ValueError("not a sealed pair key")
    epk = unb64(sealed["epk"])
    own = transport_private.public_key().public_bytes(serialization.Encoding.X962,
                                                      serialization.PublicFormat.UncompressedPoint)
    shared = transport_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), epk))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=epk + own, info=SEAL_INFO).derive(shared)
    return AESGCM(key).decrypt(unb64(sealed["nonce"]), unb64(sealed["ct"]), aad)


# ---------------------------------------------------------------------- poll proofs
def poll_message(device_code_hash: str, seconds: str, what: str = "poll") -> bytes:
    return POLL_DOMAIN + ("%s\n%s\n%s" % (what, device_code_hash, seconds)).encode("ascii")


def sign_poll(worker_key, device_code: str, what: str = "poll", now: float | None = None) -> dict:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    seconds = str(int(now if now is not None else time.time()))
    sig = worker_key.sign(poll_message(code_hash(device_code), seconds, what), ec.ECDSA(hashes.SHA256()))
    return {"device_code": device_code, "ts": seconds, "proof": b64(sig)}


def fingerprint_of_csr(csr_pem: bytes) -> str:
    """What the agent prints and the approval page shows: SHA-256 of the request key's SubjectPublicKeyInfo, grouped."""
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    spki = x509.load_pem_x509_csr(csr_pem).public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    h = sha256_hex(spki)[:32].upper()
    return " ".join(h[i:i + 4] for i in range(0, 32, 4))


# ---------------------------------------------------------------------- the agent's side
class PairRefused(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code + (": " + detail if detail else ""))
        self.code = code
        self.detail = detail


def _post(url, body: dict, *, context, connect=None, timeout: float = 15.0) -> tuple[int, dict]:
    """One HTTPS POST to the pinned public name -- the same strict outer TLS as the slot path, no redirects."""
    import http.client
    import socket as _socket

    from agentnode_sdk.worker import edge as E

    data = json.dumps(body, sort_keys=True).encode("utf-8")
    raw = (connect or _socket.create_connection)((url.host, url.port), timeout)
    try:
        try:
            tls = context.wrap_socket(raw, server_hostname=url.host)
        except Exception as exc:                               # noqa: BLE001 - classified by name
            import ssl
            if isinstance(exc, ssl.SSLCertVerificationError):
                raise PairRefused("control_tls_untrusted") from None
            raise PairRefused("control_unreachable", type(exc).__name__) from None
        conn = http.client.HTTPSConnection(url.host, url.port, timeout=timeout, context=context)
        conn.sock = tls
        conn.request("POST", url.path, body=data, headers={"Host": url.host, "Content-Type": "application/json",
                                                           "Accept": "application/json", "Connection": "close"})
        resp = conn.getresponse()
        payload = resp.read(1 << 20)
        try:
            doc = json.loads(payload.decode("utf-8")) if payload else {}
        except ValueError:
            doc = {}
        return resp.status, doc if isinstance(doc, dict) else {}
    except OSError as exc:
        raise PairRefused("control_unreachable", type(exc).__name__) from None
    finally:
        try:
            raw.close()
        except OSError:
            pass


def new_material(state_dir) -> dict:
    """Worker key + CSR + transport key in `state_dir` (0700), each 0600 and created exclusively."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    d = Path(state_dir)
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(str(d), 0o700)
    except OSError:
        pass

    def keyfile(name):
        p = d / name
        if p.exists():
            return serialization.load_pem_private_key(p.read_bytes(), None)
        k = ec.generate_private_key(ec.SECP256R1())
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
        return k
    worker = keyfile("key.pem")
    transport = keyfile("transport-key.pem")
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "request")]))
           .sign(worker, hashes.SHA256())).public_bytes(serialization.Encoding.PEM)
    tpub = transport.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"worker": worker, "transport": transport, "csr": csr, "transport_pub": tpub}


def _write_0600(path: Path, data: bytes) -> None:
    tmp = path.with_name("." + path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp), str(path))


def install_result(result: dict, material: dict, pins: dict, state_dir, device_code: str) -> dict:
    """Check everything against the pins, open the sealed pair key, and write the worker's files (each 0600).
    Returns the verified target document."""
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from agentnode_sdk.pki import identity as _identity
    from agentnode_sdk.worker import pins as _pins

    d = Path(state_dir)
    try:
        target = _pins.check_target(result["target"], pins)
        anchor = str(result["anchor"]).encode("ascii")
        _pins.check_anchor(anchor, pins)
        cert = x509.load_pem_x509_certificate(str(result["certificate"]).encode("ascii"))
        anchor_cert = x509.load_pem_x509_certificate(anchor)
        cert.verify_directly_issued_by(anchor_cert)
    except _pins.Untrusted:
        raise
    except Exception as exc:                                   # noqa: BLE001 - anything malformed is untrusted
        raise _pins.Untrusted("the pairing material is malformed (%s)" % type(exc).__name__) from None
    want_spki = material["worker"].public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    if cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo) != want_spki:
        raise _pins.Untrusted("the certificate is not for this worker's key")
    uris = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(
        x509.UniformResourceIdentifier)
    expected = "agentnode://%s/worker/%s" % (target["deployment"], target["instance"])
    if uris != [expected] or _identity.parse(uris[0]).role != _identity.WORKER:
        raise _pins.Untrusted("the certificate names another identity than target.json")
    aad = seal_aad(target["target_id"], target["instance"], code_hash(device_code))
    try:
        ring = unseal(result["pair_key_sealed"], material["transport"], aad)
        from agentnode_sdk.worker import pairkeys
        tmp = d / ".keyring.check"
        tmp.write_bytes(ring)
        try:
            parsed = pairkeys.Keyring.read(str(tmp))
        finally:
            tmp.unlink()
        if set(parsed.pairs()) != {(target["accept_gateway"], target["instance"])}:
            raise ValueError("pairs")
    except Exception:                                          # noqa: BLE001
        raise _pins.Untrusted("the pair key does not open for this worker or names another pair") from None
    _write_0600(d / "cert.pem", str(result["certificate"]).encode("ascii"))
    _write_0600(d / "ca.pem", anchor)
    _write_0600(d / "revoked.crl", unb64(result["crl"]))
    _write_0600(d / "revoked-identities.json", str(result["tombstones"]).encode("utf-8"))
    _write_0600(d / "target.json", (json.dumps(target, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    _write_0600(d / "keyring.json", ring)
    return target


def pair(pins_path, state_dir, *, name: str, concurrency: int = 1, queue: int = 2, say=print, context=None,
         connect=None, platform: dict | None = None, wait_seconds: float = 900.0, sleep=time.sleep) -> dict:
    """The whole agent side. Returns the verified target document; raises PairRefused / pins.Untrusted by name."""
    from agentnode_sdk.worker import edge as E
    from agentnode_sdk.worker import pins as _pins

    pins = _pins.read_pins(pins_path)
    E.refuse_configured_proxy()
    url = E.control_url(pins["control_url"])
    ctx = context or E.outer_context()
    material = new_material(state_dir)

    def at(path):
        return E.ControlUrl(host=url.host, port=url.port, path=path)
    from agentnode_sdk.worker.dialback import platform_report
    status, doc = _post(at(START_PATH), {"protocol": PROTOCOL, "csr": material["csr"].decode("ascii"),
                                         "transport_key": b64(material["transport_pub"]),
                                         "name": str(name)[:64], "concurrency": int(concurrency), "queue": int(queue),
                                         "platform": platform if platform is not None else platform_report()},
                        context=ctx, connect=connect)
    if status != 200:
        raise PairRefused(str(doc.get("error") or "pair_start_refused_%d" % status))
    device_code, interval = str(doc["device_code"]), int(doc.get("interval") or 5)
    say("")
    say("  To connect this machine to your AgentNode account, open in your browser:")
    say("      %s" % doc["verification_uri"])
    say("  sign in, and enter the code:  %s" % doc["user_code"])
    say("  Check that the page shows this machine's key fingerprint:")
    say("      %s" % fingerprint_of_csr(material["csr"]))
    say("  The code expires in %d minutes. Nothing is installed or run until you approve it." % (int(doc["expires_in"]) // 60))
    ends = time.monotonic() + min(float(wait_seconds), float(doc["expires_in"]) + 30)
    while time.monotonic() < ends:
        sleep(interval)
        status, got = _post(at(POLL_PATH), sign_poll(material["worker"], device_code), context=ctx, connect=connect)
        error = str(got.get("error") or "")
        if status == 200 and got.get("result"):
            target = install_result(got["result"], material, pins, state_dir, device_code)
            _post(at(ACK_PATH), sign_poll(material["worker"], device_code, "ack"), context=ctx, connect=connect)
            (Path(state_dir) / "transport-key.pem").unlink()
            say("  Paired: this machine is target %s of your account (device %s)." % (target["target_id"],
                                                                                     target["device_id"]))
            return target
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise PairRefused(error or "pair_poll_refused_%d" % status)
    raise PairRefused("pair_code_expired", "nobody approved the code in time")


RETIRE_PATH = "/agent/v1/device/retire"


def retire_request(state_dir, *, now: float | None = None) -> dict:
    """The signed request an uninstalling worker sends to retire itself: deployment, instance, target, device, a fresh
    request id and the time, signed with the worker key, with its certificate. Root verifies it again."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    from agentnode_sdk.remote_mcp import agent_service as _as

    d = Path(state_dir)
    target = json.loads((d / "target.json").read_text(encoding="utf-8"))
    key = serialization.load_pem_private_key((d / "key.pem").read_bytes(), None)
    der = x509.load_pem_x509_certificate((d / "cert.pem").read_bytes()).public_bytes(serialization.Encoding.DER)
    req = {"deployment": target["deployment"], "instance": target["instance"], "target_id": target["target_id"],
           "device_id": target["device_id"], "request_id": os.urandom(16).hex(),
           "ts": str(int(now if now is not None else time.time())), "certificate": b64(der)}
    req["proof"] = b64(key.sign(_as.retire_message(dict(req, proof="")), ec.ECDSA(hashes.SHA256())))
    return req


def send_retire(pins_path, state_dir, *, context=None, connect=None) -> str:
    """Ask the control plane to retire this worker (uninstall --revoke). Returns the outcome code."""
    from agentnode_sdk.worker import edge as E
    from agentnode_sdk.worker import pins as _pins

    pins = _pins.read_pins(pins_path)
    url = E.control_url(pins["control_url"])
    status, doc = _post(E.ControlUrl(host=url.host, port=url.port, path=RETIRE_PATH), retire_request(state_dir),
                        context=context or E.outer_context(), connect=connect)
    return "accepted" if status == 200 and doc.get("status") == "accepted" else str(doc.get("error") or status)


__all__ = ["ACK_PATH", "PAGE_PATH", "POLL_PATH", "PROTOCOL", "PairRefused", "START_PATH", "b64", "code_hash",
           "fingerprint_of_csr", "install_result", "new_material", "pair", "poll_message", "seal", "seal_aad",
           "sign_poll", "unb64", "unseal"]
