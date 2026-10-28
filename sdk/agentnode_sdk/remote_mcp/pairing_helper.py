"""The root pairing helper `rmcp-pairing` (local-worker slice 2, Codex Q-LW2-0001 D6 as amended by Q-LW2-0003/0004).

Pairing must issue a certificate and sign a register generation while no operator types anything. The process that
serves `/agent/*` (`rmcp-agent`) is unprivileged and may hold neither the operator key nor the CA. This helper holds
nothing new -- it uses the issuer and the operator key the root timers already use -- and it exposes exactly ONE
operation to exactly ONE caller:

    socket  /run/rmcp-pairing/helper.sock   root:rmcp-pairing 0660, in a root-owned 0750 directory
    caller  SO_PEERCRED uid == the rmcp-agent uid (uid 0 included: refused)
    request {"op": "materialize", "authorization_id": "<64 hex>"}\\n        (one line, <= 4096 bytes)
    answer  {"ok": true, "result": {...}} | {"ok": false, "code": "<name>"}\\n

`materialize` re-reads the authorization rmcp-agent stored for that id and believes none of it without checking it
against the gateway itself: the approving device is a person's device of that account holding MANAGE_DEVICES, the
account is active, and the approving browser session exists, is that device's and was OPENED at most 300 s before
the approval (Q-LW2-0002 Q2). Then `Operations.pair` -- one fsynced, resumable operation with ids derived from the
authorization -- makes the worker device, the target, the certificate for the agent's own key, the pair key and the
sealed result. A repeated call returns the same result; an open operation left by a crash is completed at start.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import stat
import struct
import sys
import time
from pathlib import Path

AUTH_SCHEMA = "agentnode.pair-authorization/1"
AUTH_KEYS = ("schema", "authorization_id", "device_code_hash", "account", "approver_device", "approver_session",
             "approved_at", "expires_at", "csr", "spki_sha256", "transport_key", "name", "concurrency", "queue")
AUTHORIZATION_SECONDS = 600
RECENT_SESSION_SECONDS = 300
MAX_CONCURRENCY, MAX_QUEUE = 4, 8
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[A-Za-z0-9 ._-]{1,64}\Z")


class HelperRefused(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code + (": " + detail if detail else ""))
        self.code = code
        self.detail = detail


def validate_authorization(doc: dict, authorization_id: str, *, now: float) -> dict:
    """The closed shape of an authorization and everything that can be checked without the gateway."""
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from agentnode_sdk.gateway import accounts as _accounts
    from agentnode_sdk.worker import pairing as _pairing

    if not isinstance(doc, dict) or set(doc) != set(AUTH_KEYS) or doc["schema"] != AUTH_SCHEMA \
            or doc["authorization_id"] != authorization_id:
        raise HelperRefused("pair_authorization_invalid", "shape")
    if not (_HEX64.match(str(doc["device_code_hash"])) and _HEX64.match(str(doc["approver_session"]))
            and _HEX64.match(str(doc["spki_sha256"])) and _accounts.well_formed(str(doc["account"]))
            and re.match(r"[0-9a-f]{16}\Z", str(doc["approver_device"])) and _NAME.match(str(doc["name"]))):
        raise HelperRefused("pair_authorization_invalid", "fields")
    for k, top in (("concurrency", MAX_CONCURRENCY), ("queue", MAX_QUEUE)):
        if not isinstance(doc[k], int) or isinstance(doc[k], bool) or not 1 <= doc[k] <= top:
            raise HelperRefused("pair_authorization_invalid", k)
    approved, expires = float(doc["approved_at"]), float(doc["expires_at"])
    if not (approved <= now + 5 and expires - approved <= AUTHORIZATION_SECONDS and now < expires):
        raise HelperRefused("pair_code_expired", "the authorization is not current")
    try:
        csr = x509.load_pem_x509_csr(str(doc["csr"]).encode("ascii"))
        key = csr.public_key()
        if not csr.is_signature_valid or not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
            raise ValueError("csr")
        spki = key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        if _pairing.sha256_hex(spki) != doc["spki_sha256"]:
            raise ValueError("spki")
        raw = _pairing.unb64(doc["transport_key"])
        if len(raw) != 65:
            raise ValueError("transport")
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    except (ValueError, TypeError):
        raise HelperRefused("pair_authorization_invalid", "keys") from None
    return doc


def check_against_gateway(doc: dict, gateway_dir) -> None:
    """The approval as the GATEWAY sees it now (root reads it directly)."""
    from agentnode_sdk.access import contract
    from agentnode_sdk.access.dispatch import what_it_may_do
    from agentnode_sdk.gateway import accounts as _accounts
    from agentnode_sdk.gateway.identity import GatewayState

    state = GatewayState(str(gateway_dir), "pairing-helper")
    record = next((r for r in state.paired_clients() if r.get("client_id") == doc["approver_device"]), None)
    if record is None or record.get("kind") == "worker-device":
        raise HelperRefused("pair_not_permitted", "the approving device is not a person's device")
    if str(record.get("account_id") or "") != doc["account"]:
        raise HelperRefused("pair_not_permitted", "the approving device is in another account")
    if contract.MANAGE_DEVICES not in what_it_may_do(record):
        raise HelperRefused("pair_not_permitted", "the approving device may not manage devices")
    try:
        active = state.accounts.get(doc["account"]).state == _accounts.ACTIVE
    except Exception:                                          # noqa: BLE001
        active = False
    if not active:
        raise HelperRefused("pair_account_inactive")
    sessions = json.loads((Path(gateway_dir) / "sessions.json").read_text(encoding="utf-8"))
    found = sessions.get(doc["approver_session"]) if isinstance(sessions, dict) else None
    if not found or found.get("client_id") != doc["approver_device"]:
        raise HelperRefused("pair_not_permitted", "the approving session is gone")
    opened = float(found.get("opened_at") or 0)
    if not 0 <= float(doc["approved_at"]) - opened <= RECENT_SESSION_SECONDS:
        raise HelperRefused("pair_not_permitted", "the approving session was not opened within 300 s of approval")


def peer_uid(conn) -> int:
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", creds)[1]


class Helper:
    def __init__(self, ops, *, gateway_dir, store, allowed_uid: int, clock=time.time) -> None:
        self.ops = ops
        self.gateway_dir = str(gateway_dir)
        self.store = Path(store)
        self.allowed_uid = int(allowed_uid)
        self.clock = clock
        if self.allowed_uid == 0:
            raise ValueError("the helper's caller is the unprivileged rmcp-agent, never root")

    def materialize(self, authorization_id: str) -> dict:
        if not _HEX64.match(str(authorization_id or "")):
            raise HelperRefused("pair_authorization_invalid", "id")
        path = self.store / "authorizations" / (authorization_id + ".json")
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise HelperRefused("pair_authorization_unknown") from None
        op_id, _tid, _inst = self.ops.pair_ids(authorization_id)
        done = self.ops.ops_dir / (op_id + ".json")
        if not done.exists():
            # only a NEW materialization is judged against the clock and the gateway; a finished or interrupted one
            # is the same authorization, already judged, and returns / completes the same result
            validate_authorization(doc, authorization_id, now=self.clock())
            check_against_gateway(doc, self.gateway_dir)
        else:
            validate_authorization(doc, authorization_id, now=float(doc.get("approved_at") or 0))
        from agentnode_sdk.remote_mcp.target_ops import OperationFailed
        try:
            op = self.ops.pair(doc)
        except OperationFailed as exc:
            raise HelperRefused("pair_materialize_failed", exc.code) from None
        if op["state"] != "done" or "result" not in op["params"]:
            raise HelperRefused("pair_materialize_failed", op.get("error") or op["state"])
        return op["params"]["result"]

    def answer(self, conn) -> None:
        """One request on one accepted connection."""
        try:
            if peer_uid(conn) != self.allowed_uid:
                reply = {"ok": False, "code": "helper_caller_refused"}
            else:
                conn.settimeout(10.0)
                line = b""
                while not line.endswith(b"\n") and len(line) <= 4096:
                    chunk = conn.recv(1)
                    if not chunk:
                        break
                    line += chunk
                try:
                    ask = json.loads(line.decode("utf-8"))
                except ValueError:
                    ask = None
                if not isinstance(ask, dict) or set(ask) != {"op", "authorization_id"} or ask["op"] != "materialize":
                    reply = {"ok": False, "code": "helper_operation_refused"}
                else:
                    try:
                        reply = {"ok": True, "result": self.materialize(str(ask["authorization_id"]))}
                    except HelperRefused as refused:
                        reply = {"ok": False, "code": refused.code}
            conn.sendall((json.dumps(reply, sort_keys=True) + "\n").encode("utf-8"))
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass


def serve(helper: Helper, path: str, group: str) -> None:
    """Serve one request at a time (pairings are rare; one at a time makes every claim serial) until killed."""
    import grp

    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    os.chown(directory, 0, grp.getgrnam(group).gr_gid)
    os.chmod(directory, 0o750)
    if os.path.lexists(path):
        if not stat.S_ISSOCK(os.lstat(path).st_mode):
            raise RuntimeError("%s exists and is not a socket" % path)
        os.unlink(path)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        s.bind(path)
    finally:
        os.umask(old)
    os.chown(path, 0, grp.getgrnam(group).gr_gid)
    os.chmod(path, 0o660)
    s.listen(16)
    print("rmcp-pairing: serving materialize() to uid %d on %s" % (helper.allowed_uid, path), flush=True)
    while True:
        conn, _ = s.accept()
        helper.answer(conn)


def call(path: str, authorization_id: str, *, timeout: float = 120.0) -> dict:
    """rmcp-agent's side: materialize one authorization. Returns the result or raises HelperRefused."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
        s.sendall((json.dumps({"op": "materialize", "authorization_id": authorization_id}) + "\n").encode("ascii"))
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
            if len(data) > (1 << 20):
                raise HelperRefused("helper_unavailable", "answer too large")
        reply = json.loads(data.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise HelperRefused("helper_unavailable", type(exc).__name__) from None
    finally:
        s.close()
    if not reply.get("ok"):
        raise HelperRefused(str(reply.get("code") or "helper_refused"))
    return reply["result"]


def main(argv=None) -> int:
    import pwd

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from agentnode_sdk.gateway.identity import GatewayState
    from agentnode_sdk.remote_mcp.targets_cli import build

    p = argparse.ArgumentParser(prog="rmcp-pairing")
    p.add_argument("--stand", required=True)
    p.add_argument("--gateway-dir", required=True)
    p.add_argument("--store", required=True, help="rmcp-agent's pairing store (authorizations/ is read)")
    p.add_argument("--socket", required=True)
    p.add_argument("--group", required=True, help="the group the socket is shared with (only rmcp-agent in it)")
    p.add_argument("--caller", required=True, help="the user name whose uid alone may call")
    p.add_argument("--control-url", required=True, help="https://<name>/agent/v1/slot, as the release pins it")
    args = p.parse_args(argv)
    if os.geteuid() != 0:
        print("rmcp-pairing runs as root", file=sys.stderr)
        return 2
    from agentnode_sdk.worker import edge as E
    control = E.control_url(args.control_url).text
    ops, _cfg, _tls = build(args.stand, "127.0.0.1", gateway_dir=args.gateway_dir)
    state = GatewayState(args.gateway_dir, "pairing-helper")
    ops.pairing_device = lambda account, name, op_id: state.add_worker_device(account, name, op_id)
    ops.pairing_signer = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(open(os.path.join(args.stand, "operator_priv.hex"), encoding="utf-8").read().strip()))
    ops.pairing_control = control
    resumed = ops.resume(kinds=("pair",))
    if resumed:
        print("rmcp-pairing: completed interrupted pairing(s): %s" % ", ".join(resumed), flush=True)
    helper = Helper(ops, gateway_dir=args.gateway_dir, store=args.store, allowed_uid=pwd.getpwnam(args.caller).pw_uid)
    serve(helper, args.socket, args.group)
    return 0


if __name__ == "__main__":
    sys.exit(main())
