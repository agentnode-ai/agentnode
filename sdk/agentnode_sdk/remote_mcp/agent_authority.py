"""The narrow, read-only view of the gateway that the unprivileged agent-facing service judges by (local-worker
slice 2, Codex Q-LW2-0003 Q1: "read-only access only to the minimum named authority fields -- preferably through
narrowly permissioned projections rather than unrestricted gateway token/session files").

`rmcp-agent` serves everything an agent or a pairing browser reaches, as an unprivileged account. It may not read
the gateway directory: that holds the gateway's configuration, ledger and identity beside the authority files, and
the gateway's own session reader WRITES on every read (`Sessions.whose` refreshes `last_used`). So root projects,
on every change of the gateway's files (a systemd path unit) and at least every few seconds (a timer), exactly this:

    {"schema": "agentnode.agent-authority/1", "generated_at", "stopped": "" | reason,
     "accounts": {account_id: "active" | "suspended"},
     "devices":  {client_id: {"account_id", "kind": "person" | "worker-device", "capabilities": [...] , "name"}},
     "sessions": {sha256(session id): {"client_id", "opened_at", "last_used", "ends_at"}}}

No token hash, no CSRF value, no pairing or joining code, nothing presentable. The reader refuses a projection older
than `MAX_AGE_SECONDS` or unreadable (`AuthorityUnavailable` -> `edge_authority_unavailable` / 503): stale authority
is never treated as current.

`reverse_device_error` is the dial-back device rule shared with the root control plane (`accounts_login`): the
device exists, belongs to the target's owner, the owner is active, and it is either a WORKER DEVICE (made by pairing,
`kind: worker-device`, capabilities `[]`) or -- slice 1's operator-invited targets -- a device holding RUN.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

SCHEMA = "agentnode.agent-authority/1"
MAX_AGE_SECONDS = 30.0
WORKER_DEVICE = "worker-device"
PERSON = "person"
RUN, MANAGE_DEVICES = "run", "manage_devices"


class AuthorityUnavailable(Exception):
    """The projection cannot be believed right now; refuse, never assume."""


# ---------------------------------------------------------------------- root's side
def project(gateway_dir, *, now: float | None = None) -> dict:
    """Read the gateway's authority files (root) and return the projection. Raises on anything unreadable."""
    from agentnode_sdk.access import contract
    from agentnode_sdk.access.dispatch import what_it_may_do
    from agentnode_sdk.gateway import accounts as _accounts
    from agentnode_sdk.gateway import allowance
    from agentnode_sdk.gateway.identity import GatewayState

    state = GatewayState(str(gateway_dir), "agent-authority")
    devices = {}
    for record in state.paired_clients():
        client_id = str(record.get("client_id") or "")
        if not client_id:
            continue
        caps = what_it_may_do(record)
        devices[client_id] = {"account_id": str(record.get("account_id") or _accounts.solo_account_for(client_id)),
                              "kind": WORKER_DEVICE if record.get("kind") == WORKER_DEVICE else PERSON,
                              "capabilities": sorted(c for c in caps if c in contract.CAPABILITIES),
                              "name": str(record.get("client_name") or "")[:64]}
    accounts = {}
    for acct in {d["account_id"] for d in devices.values()}:
        try:
            accounts[acct] = "active" if state.accounts.get(acct).state == _accounts.ACTIVE else "suspended"
        except Exception:                                      # noqa: BLE001 - unreadable is not active
            accounts[acct] = "suspended"
    sessions = {}
    path = Path(gateway_dir) / "sessions.json"
    if path.exists():
        kept = json.loads(path.read_text(encoding="utf-8"))
        for named, found in (kept if isinstance(kept, dict) else {}).items():
            sessions[str(named)] = {k: found.get(k) for k in ("client_id", "opened_at", "last_used", "ends_at")}
    try:
        stopped = allowance.why_it_is_stopped(str(gateway_dir)) or ""
    except Exception as exc:                                   # noqa: BLE001 - unreadable is stopped
        stopped = "unreadable: " + type(exc).__name__
    return {"schema": SCHEMA, "generated_at": float(now if now is not None else time.time()), "stopped": stopped,
            "accounts": accounts, "devices": devices, "sessions": sessions}


def write(doc: dict, out, *, group: str = "") -> None:
    """Atomically, 0640, readable by `group` (the agent-facing service's group) and nobody else."""
    out = Path(out)
    tmp = out.with_name(".%s.%d.tmp" % (out.name, os.getpid()))
    data = (json.dumps(doc, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    if group:
        import grp
        os.chown(str(tmp), 0, grp.getgrnam(group).gr_gid)
    os.chmod(str(tmp), 0o640)
    os.replace(str(tmp), str(out))


# ---------------------------------------------------------------------- the unprivileged reader
class Authority:
    def __init__(self, path, *, max_age: float = MAX_AGE_SECONDS, clock=time.time) -> None:
        self.path = Path(path)
        self.max_age = float(max_age)
        self.clock = clock
        self._cache: tuple | None = None

    def load(self) -> dict:
        try:
            st = self.path.stat()
            key = (st.st_mtime_ns, st.st_size)
            if self._cache is None or self._cache[0] != key:
                doc = json.loads(self.path.read_bytes().decode("utf-8"))
                if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
                    raise ValueError("schema")
                self._cache = (key, doc)
            doc = self._cache[1]
        except (OSError, ValueError) as exc:
            raise AuthorityUnavailable("the authority projection cannot be read (%s)" % type(exc).__name__) from None
        age = self.clock() - float(doc.get("generated_at") or 0)
        if not (-5.0 <= age <= self.max_age):
            raise AuthorityUnavailable("the authority projection is %.0f s old" % age)
        return doc

    def stopped(self) -> str:
        return str(self.load().get("stopped") or "")

    def device(self, client_id: str) -> dict | None:
        return (self.load().get("devices") or {}).get(str(client_id))

    def account_active(self, account_id: str) -> bool:
        return (self.load().get("accounts") or {}).get(str(account_id)) == "active"

    def session(self, session_id: str) -> dict | None:
        """The session the cookie names, if it is not over (the gateway's own two expiries); None otherwise."""
        from agentnode_sdk.access.sessions import GOOD_FOR_SECONDS, fingerprint

        if not session_id:
            return None
        found = (self.load().get("sessions") or {}).get(fingerprint(session_id))
        if not found:
            return None
        now = self.clock()
        if now - float(found.get("last_used") or 0) > GOOD_FOR_SECONDS or now >= float(found.get("ends_at") or 0):
            return None
        return dict(found, session=fingerprint(session_id))

    def reverse_device_error(self, account: str, device: str) -> str:
        """"" or the routing code for a dial-back target's (owner, device), from this projection."""
        return reverse_device_rule(self.device(device), account, self.account_active)


def reverse_device_rule(record: dict | None, account: str, account_active) -> str:
    if not account or not record:
        return "target_device_revoked"
    if str(record.get("account_id") or "") != str(account):
        return "target_device_revoked"
    if not account_active(str(account)):
        return "target_account_suspended"
    if record.get("kind") == WORKER_DEVICE:
        return ""
    return "" if RUN in (record.get("capabilities") or ()) else "target_device_revoked"


def mirror(stand, tls, out_dir, *, group: str = "") -> list[str]:
    """Copy what the agent-facing service verifies for itself into `out_dir` (0640, `group`): the SIGNED register
    (`targets/targets.json`, `head.json`, `history/<g>.json`) -- verified there with the operator public key, so a
    copy is as trustworthy as the original and nothing unsigned travels -- and the deployment's public trust material
    (anchor, revocation list, tombstones, the gateway role's time floor). Never the bundles, the operations journal,
    a key or a secret. Returns the files written."""
    import shutil

    out = Path(out_dir)
    written = []

    def put(src: Path, rel: str):
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(".%s.tmp" % dst.name)
        shutil.copyfile(str(src), str(tmp))
        if group:
            import grp
            os.chown(str(tmp), 0, grp.getgrnam(group).gr_gid)
        os.chmod(str(tmp), 0o640)
        os.replace(str(tmp), str(dst))
        written.append(rel)
    targets = Path(stand) / "targets"
    for name in ("targets.json", "head.json", "clock.json"):
        if (targets / name).exists():
            put(targets / name, "targets/" + name)
    history = targets / "history"
    if history.is_dir():
        for f in sorted(history.glob("*.json")):
            if not (out / "targets" / "history" / f.name).exists():          # history is append-only
                put(f, "targets/history/" + f.name)
    put(Path(stand) / "operator_pub.hex", "operator_pub.hex")
    for src, rel in ((tls.anchor, "trust/ca.pem"), (tls.revocation_list, "trust/revoked.crl"),
                     (tls.identity_tombstones, "trust/revoked-identities.json"), (tls.floor, "trust/gateway.floor")):
        put(Path(src), rel)
    return written


def main(argv=None) -> int:
    """root, on every change of the gateway or the register and on a short timer:

    python -m agentnode_sdk.remote_mcp.agent_authority --gateway-dir G --stand S --out-dir D [--group rmcp-agent]
    """
    import argparse

    p = argparse.ArgumentParser(prog="agentnode-agent-authority")
    p.add_argument("--gateway-dir", required=True)
    p.add_argument("--stand", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--group", default="")
    args = p.parse_args(argv)
    from agentnode_sdk.remote_mcp.server import _worker_base
    _cfg, tls = _worker_base(args.stand)
    mirror(args.stand, tls, args.out_dir, group=args.group)
    write(project(args.gateway_dir), Path(args.out_dir) / "authority.json", group=args.group)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
