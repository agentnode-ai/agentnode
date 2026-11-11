"""Evidence that the control plane is on a keep's generation, for a paired worker rollback (P13).

`deploy/separate-worker-host/rollback-one-host.sh` refuses to roll a WORKER back to a build whose
`ERRORS` table lacks "no-lease": such a worker reports every lease refusal as "internal", the
control plane never re-takes the lease, and the pair stops recovering. The remedy is to roll the
control plane back to the same generation FIRST. Whether that has happened used to be the
operator's word (`--control-plane-already-rolled-back`). It is now a signed statement the worker
checks itself, with nothing outside the deployment's own trust:

1. ``challenge`` -- the worker writes a single-use challenge into its own state: a random nonce,
   its identity (from its own certificate), the acceptance run, and the generation and build of
   the keep it is going back to;
2. ``sign`` -- on the control plane, AFTER its rollback was confirmed (the running process named
   the kept build), the gateway's TLS key signs: that nonce, worker, run and worker build, the
   generation read from the ERRORS table now installed there, the build now running, the
   gateway's identity and the time, with its certificate attached;
3. ``verify`` -- the worker judges the certificate exactly as it judges a gateway on a connection
   (chains to its anchor, valid at its effective time, the gateway usage, deployment, role and an
   accepted instance, not revoked, not withdrawn), checks the signature, and then every binding.
   Only a statement that passes all of it waives the guard, and passing CONSUMES the challenge,
   so the same statement can never waive it twice.

What this does not cover, said so nobody reads more into it: whoever holds the gateway's private
key can sign a false statement. That is the same key that already authenticates every instruction
the worker obeys, so this asks for no trust the pair does not already give.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import hmac
import json
import os
import secrets
import tarfile
import time
from pathlib import Path

from agentnode_sdk.pki import identity as _ids

CHALLENGE_KIND = "agentnode-rollback-challenge/1"
STATEMENT_KIND = "agentnode-rollback-statement/1"
CHALLENGE_FILE = "rollback-challenge.json"

#: How long a statement and its challenge stay usable. A paired rollback is one maintenance
#: window; a statement older than that describes a control plane that may have moved since.
MAX_AGE_SECONDS = 3600
#: The two machines' clocks are not the same clock.
CLOCK_SKEW_SECONDS = 300

NO_STATEMENT = "no-statement"
NO_CHALLENGE = "no-challenge"
MALFORMED = "malformed"
REPLAYED = "replayed"
WRONG_CHALLENGE = "wrong-challenge"
WRONG_RUN = "wrong-run"
WRONG_WORKER = "wrong-worker"
WRONG_BUILD = "wrong-build"
WRONG_GENERATION = "wrong-generation"
STALE = "stale"
BAD_SIGNER = "bad-signer"
BAD_SIGNATURE = "bad-signature"
NO_GENERATION = "no-generation"

_CHALLENGE_KEYS = {"kind", "nonce", "worker", "run_id", "keep_generation", "keep_build",
                   "issued_at"}
_BODY_KEYS = {"kind", "nonce", "worker", "gateway", "run_id", "worker_keep_build",
              "control_generation", "control_build", "issued_at"}


class ProofRefused(Exception):
    """The statement does not prove what the waiver needs. Carries which check said no."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__("%s: %s" % (code, detail))
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------------- generation


def generation_of(protocol_source: str) -> str:
    """The refusal-code generation of a `worker/protocol.py`: the codes its ERRORS TUPLE carries.

    Read from the tuple, because the defect was a name DEFINED in the file and missing from the
    tuple -- a search for "no-lease" matches every build ever shipped.
    """
    try:
        tree = ast.parse(protocol_source)
    except SyntaxError as exc:
        raise ProofRefused(NO_GENERATION, "protocol.py does not parse") from exc
    constants, errors = {}, None
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                constants[name] = node.value.value
            elif name == "ERRORS" and isinstance(node.value, ast.Tuple):
                errors = node.value.elts
    if not errors:
        raise ProofRefused(NO_GENERATION, "protocol.py has no ERRORS tuple")
    codes = []
    for element in errors:
        if isinstance(element, ast.Name) and element.id in constants:
            codes.append(constants[element.id])
        elif isinstance(element, ast.Constant) and isinstance(element.value, str):
            codes.append(element.value)
        else:
            raise ProofRefused(NO_GENERATION, "an ERRORS entry cannot be resolved to its code")
    digest = hashlib.sha256("\n".join(sorted(set(codes))).encode("utf-8")).hexdigest()
    return "errors:" + digest


def generation_of_keep(keep) -> str:
    """The generation of the package a keep-directory holds, read from its tar, not imported."""
    try:
        with tarfile.open(Path(keep) / "installed-package.tar") as tar:
            member = tar.extractfile("agentnode_sdk/worker/protocol.py")
            source = member.read().decode("utf-8") if member else ""
    except (OSError, KeyError, tarfile.TarError) as exc:
        raise ProofRefused(NO_GENERATION, "the keep's package cannot be read") from exc
    return generation_of(source)


# ---------------------------------------------------------------------------------- helpers


def _canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _certificate(pem):
    from cryptography import x509

    if isinstance(pem, str):
        pem = pem.encode("ascii")
    return x509.load_pem_x509_certificate(pem)


def _uri_of(certificate) -> str:
    sans = _ids._uri_sans(certificate)
    return sans[0] if len(sans) == 1 else ""


def _write_atomically(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as fh:
        json.dump(value, fh, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temporary, path)


def _text(value) -> bool:
    return isinstance(value, str) and bool(value)


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ---------------------------------------------------------------------------------- 1. challenge


def issue_challenge(state_dir, *, worker_certificate_pem: bytes, run_id: str,
                    keep_generation: str, keep_build: str, now: float | None = None) -> dict:
    """Write a new pending challenge into the worker's state and return it. A new challenge
    replaces any earlier one, so a statement made for the earlier one stops being usable."""
    if not (_text(run_id) and _text(keep_build) and _text(keep_generation)):
        raise ProofRefused(MALFORMED, "a challenge needs the run, the keep's build and generation")
    worker = _uri_of(_certificate(worker_certificate_pem))
    try:
        if _ids.parse(worker).role != _ids.WORKER:
            raise ProofRefused(WRONG_WORKER, "the certificate given is not a worker's")
    except _ids.NotAnIdentity as exc:
        raise ProofRefused(WRONG_WORKER, "the certificate given names no identity") from exc
    challenge = {"kind": CHALLENGE_KIND, "nonce": secrets.token_hex(32), "worker": worker,
                 "run_id": run_id, "keep_generation": keep_generation, "keep_build": keep_build,
                 "issued_at": float(time.time() if now is None else now)}
    state = Path(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    _write_atomically(state / CHALLENGE_FILE, {"challenge": challenge, "consumed": None})
    return challenge


# ---------------------------------------------------------------------------------- 2. sign


def sign_statement(challenge: dict, *, certificate_pem: bytes, key_pem: bytes,
                   control_generation: str, control_build: str,
                   now: float | None = None) -> dict:
    """The control plane's statement about itself, made out to one challenge. Signing is not
    where anything is decided -- the worker decides -- so this refuses only what it cannot sign."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    if not isinstance(challenge, dict) or set(challenge) != _CHALLENGE_KEYS \
            or challenge.get("kind") != CHALLENGE_KIND:
        raise ProofRefused(MALFORMED, "that is not a rollback challenge")
    if not (_text(control_generation) and _text(control_build)):
        raise ProofRefused(MALFORMED, "the statement needs the running generation and build")
    certificate = _certificate(certificate_pem)
    key = serialization.load_pem_private_key(key_pem, None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ProofRefused(BAD_SIGNER, "the key is not the elliptic-curve key the pair uses")
    body = {"kind": STATEMENT_KIND, "nonce": challenge["nonce"], "worker": challenge["worker"],
            "gateway": _uri_of(certificate), "run_id": challenge["run_id"],
            "worker_keep_build": challenge["keep_build"],
            "control_generation": control_generation, "control_build": control_build,
            "issued_at": float(time.time() if now is None else now)}
    signature = key.sign(_canonical(body), ec.ECDSA(hashes.SHA256()))
    from cryptography.hazmat.primitives.serialization import Encoding

    return {"body": body,
            "certificate": certificate.public_bytes(Encoding.PEM).decode("ascii"),
            "signature": base64.b64encode(signature).decode("ascii")}


# ---------------------------------------------------------------------------------- 3. verify


def _pending(state_dir) -> dict:
    path = Path(state_dir) / CHALLENGE_FILE
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProofRefused(NO_CHALLENGE, "this worker has issued no challenge; issue one, have "
                           "the control plane answer it, and bring the answer here") from exc
    except (OSError, ValueError) as exc:
        raise ProofRefused(NO_CHALLENGE, "this worker's challenge cannot be read") from exc
    challenge = record.get("challenge") if isinstance(record, dict) else None
    if not isinstance(challenge, dict) or set(challenge) != _CHALLENGE_KEYS:
        raise ProofRefused(NO_CHALLENGE, "this worker's challenge is not one")
    if record.get("consumed"):
        raise ProofRefused(REPLAYED, "this worker's challenge was already answered and used; a "
                           "statement waives the guard once")
    return record


def _shape(statement) -> tuple[dict, bytes, bytes]:
    if not statement:
        raise ProofRefused(NO_STATEMENT, "no statement from the control plane was given")
    if not isinstance(statement, dict) or set(statement) != {"body", "certificate", "signature"}:
        raise ProofRefused(MALFORMED, "that is not a control-plane statement")
    body = statement["body"]
    if not isinstance(body, dict) or set(body) != _BODY_KEYS or body.get("kind") != STATEMENT_KIND:
        raise ProofRefused(MALFORMED, "the statement's body is not one")
    for key in _BODY_KEYS - {"issued_at"}:
        if not _text(body[key]):
            raise ProofRefused(MALFORMED, "the statement's %s is empty" % key)
    if not _number(body["issued_at"]):
        raise ProofRefused(MALFORMED, "the statement carries no time")
    if not (_text(statement["certificate"]) and _text(statement["signature"])):
        raise ProofRefused(MALFORMED, "the statement is unsigned")
    try:
        signature = base64.b64decode(statement["signature"], validate=True)
    except ValueError as exc:
        raise ProofRefused(MALFORMED, "the signature is not base64") from exc
    return body, statement["certificate"].encode("ascii", "replace"), signature


def verify_and_consume(state_dir, statement, *, own_certificate_pem: bytes,
                       keep_generation: str, keep_build: str, deployment: str,
                       accept_gateways, trust, now: float | None = None) -> dict:
    """Every check, in order; the first that fails is the refusal. On success the challenge is
    marked used BEFORE this returns, so a second use of the same statement is a replay."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    body, certificate_pem, signature = _shape(statement)
    record = _pending(state_dir)
    challenge = record["challenge"]

    # Who signed it: the same judgement a gateway gets on a connection, plus the chain, which a
    # connection gets from the handshake and a file does not.
    try:
        certificate = _certificate(certificate_pem)
        certificate.verify_directly_issued_by(trust.anchor())
        der = certificate.public_bytes(serialization.Encoding.DER)
        signer = _ids.check_peer(der, deployment=deployment, expected_role=_ids.GATEWAY,
                                 accept_instances=accept_gateways, trust=trust)
    except _ids.PeerRefused as exc:
        raise ProofRefused(BAD_SIGNER, "the signer is not a gateway this worker accepts (%s)"
                           % exc.check) from exc
    except Exception as exc:                                  # noqa: BLE001 - every other way is no
        raise ProofRefused(BAD_SIGNER, "the signer's certificate is not from this deployment's "
                           "CA") from exc
    public = certificate.public_key()
    if not isinstance(public, ec.EllipticCurvePublicKey):
        raise ProofRefused(BAD_SIGNER, "the signer's key is not the kind the pair uses")
    try:
        public.verify(signature, _canonical(body), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise ProofRefused(BAD_SIGNATURE, "the statement was changed after it was signed, or "
                           "was not signed by that certificate") from exc
    if body["gateway"] != signer.uri():
        raise ProofRefused(BAD_SIGNER, "the statement names another gateway than its signer")

    # What it is about.
    me = _uri_of(_certificate(own_certificate_pem))
    if body["worker"] != me or challenge["worker"] != me:
        raise ProofRefused(WRONG_WORKER, "the statement is made out to %s and this worker is %s"
                           % (body["worker"], me))
    if not hmac.compare_digest(body["nonce"], challenge["nonce"]):
        raise ProofRefused(WRONG_CHALLENGE, "the statement answers another challenge than the "
                           "one pending here")
    if body["run_id"] != challenge["run_id"]:
        raise ProofRefused(WRONG_RUN, "the statement is for run %s and the challenge for %s"
                           % (body["run_id"], challenge["run_id"]))
    if not (body["worker_keep_build"] == challenge["keep_build"] == keep_build):
        raise ProofRefused(WRONG_BUILD, "the statement is about worker build %s and this rollback "
                           "goes to %s" % (body["worker_keep_build"], keep_build))
    if challenge["keep_generation"] != keep_generation:
        raise ProofRefused(WRONG_GENERATION, "the keep being applied is not the generation the "
                           "challenge was issued for")
    if body["control_generation"] != keep_generation:
        raise ProofRefused(WRONG_GENERATION, "the control plane runs another refusal-code "
                           "generation than the keep: the pair would stop recovering")

    # When.
    moment = float(trust.effective_time(me) if now is None else now)
    issued, asked = float(body["issued_at"]), float(challenge["issued_at"])
    if issued < asked - CLOCK_SKEW_SECONDS or issued > moment + CLOCK_SKEW_SECONDS:
        raise ProofRefused(STALE, "the statement is dated outside its challenge's window")
    if moment - issued > MAX_AGE_SECONDS or moment - asked > MAX_AGE_SECONDS + CLOCK_SKEW_SECONDS:
        raise ProofRefused(STALE, "the statement is older than %d seconds; issue a new challenge"
                           % MAX_AGE_SECONDS)

    record["consumed"] = {"at": moment,
                          "statement_sha256": hashlib.sha256(_canonical(statement)).hexdigest()}
    _write_atomically(Path(state_dir) / CHALLENGE_FILE, record)
    return dict(body)


# ---------------------------------------------------------------------------------- command line


def _env(path) -> dict:
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def _worker_trust(env: dict):
    from agentnode_sdk.pki.trust import TrustView

    tls = Path(env["AGENTNODE_TLS_DIR"])
    me = _uri_of(_certificate((tls / "cert.pem").read_bytes()))
    return (tls, me, TrustView.read(anchor=env["AGENTNODE_TRUST"],
                                    revocation_list=env["AGENTNODE_REVOCATION_LIST"],
                                    floor=env["AGENTNODE_FLOOR"], role=_ids.WORKER, identity=me,
                                    identity_tombstones=env["AGENTNODE_TOMBSTONES"],
                                    tombstones_required=True))


def _keep_build(keep) -> str:
    try:
        return (Path(keep) / "build-id.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m agentnode_sdk.worker.rollback_proof")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("challenge", help="worker: issue a single-use challenge")
    c.add_argument("--env", default="/etc/agentnode/worker.env")
    c.add_argument("--state", default="/var/lib/agentnode-worker")
    c.add_argument("--keep", required=True)
    c.add_argument("--run-id", required=True)
    c.add_argument("--out", required=True)
    s = sub.add_parser("sign", help="control plane: answer a challenge after its own rollback")
    s.add_argument("--challenge", required=True)
    s.add_argument("--certificate", required=True)
    s.add_argument("--key", required=True)
    s.add_argument("--site", required=True, help="the site-packages now installed")
    s.add_argument("--build", required=True, help="the build the running process announced")
    s.add_argument("--out", required=True)
    v = sub.add_parser("verify", help="worker: check a statement and use up the challenge")
    v.add_argument("--env", default="/etc/agentnode/worker.env")
    v.add_argument("--state", default="/var/lib/agentnode-worker")
    v.add_argument("--keep", required=True)
    v.add_argument("--statement", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "challenge":
            env = _env(args.env)
            tls, _me, _trust = _worker_trust(env)
            challenge = issue_challenge(
                args.state, worker_certificate_pem=(tls / "cert.pem").read_bytes(),
                run_id=args.run_id, keep_generation=generation_of_keep(args.keep),
                keep_build=_keep_build(args.keep))
            Path(args.out).write_text(json.dumps(challenge, sort_keys=True), encoding="utf-8")
            print("challenge for %s, run %s, keep build %s -> %s"
                  % (challenge["worker"], challenge["run_id"], challenge["keep_build"], args.out))
        elif args.command == "sign":
            challenge = json.loads(Path(args.challenge).read_text(encoding="utf-8"))
            protocol = Path(args.site) / "agentnode_sdk" / "worker" / "protocol.py"
            statement = sign_statement(
                challenge, certificate_pem=Path(args.certificate).read_bytes(),
                key_pem=Path(args.key).read_bytes(),
                control_generation=generation_of(protocol.read_text(encoding="utf-8")),
                control_build=args.build)
            Path(args.out).write_text(json.dumps(statement, sort_keys=True), encoding="utf-8")
            print("statement by %s for %s, run %s -> %s" % (
                statement["body"]["gateway"], statement["body"]["worker"],
                statement["body"]["run_id"], args.out))
        else:
            env = _env(args.env)
            tls, _me, trust = _worker_trust(env)
            try:
                statement = json.loads(Path(args.statement).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                statement = None
            body = verify_and_consume(
                args.state, statement, own_certificate_pem=(tls / "cert.pem").read_bytes(),
                keep_generation=generation_of_keep(args.keep), keep_build=_keep_build(args.keep),
                deployment=env["AGENTNODE_DEPLOYMENT"],
                accept_gateways=env["AGENTNODE_GATEWAY_INSTANCE"].split(), trust=trust)
            print("VERIFIED: %s runs %s (build %s) for %s, run %s" % (
                body["gateway"], body["control_generation"], body["control_build"],
                body["worker"], body["run_id"]))
    except ProofRefused as refused:
        print("REFUSED %s" % refused)
        return 1
    except (OSError, KeyError, ValueError) as exc:
        print("REFUSED %s: %s" % (MALFORMED, type(exc).__name__))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
