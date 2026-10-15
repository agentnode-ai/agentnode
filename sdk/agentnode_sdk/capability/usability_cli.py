"""The documented CLI for the Slice-2 closed-beta usability flow.

One self-contained front door a new user follows end to end (see
``sdk/agentnode_sdk/capability/USABILITY.md``):

    import-npm   vendor + import a REAL upstream npm MCP server, byte-unchanged, into a stand
    list/search/describe   inspect provenance, version, signature/digest, model, permissions,
                           and the achievable guarantees computed from the configured worker
    config       emit a directly-usable generic MCP-client config (and Claude Code / Codex examples)
    prepare      show the canonical invocation_hash a human must approve
    approve      operator-only: deposit a single-use approval bound to one invocation_hash
    run          prepare -> (approval) -> submit on the worker; prints result+manifest+job+audit

A "stand" is a directory holding the local index, the operator keypair (the human channel),
the audit signer key, deposited approvals and the durable single-use markers. The CLI never
self-approves: ``run`` only consumes an approval an operator issued with ``approve``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentnode_sdk.capability import client_config
from agentnode_sdk.capability.approval import ApprovalError, ApprovalStore, grant
from agentnode_sdk.capability.audit import AuditLedger
from agentnode_sdk.capability.index import LocalIndex
from agentnode_sdk.capability.usability import UsabilityClient


def _raw_pub(priv: Ed25519PrivateKey) -> bytes:
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _ensure_stand(stand: str) -> None:
    s = Path(stand)
    (s / "index").mkdir(parents=True, exist_ok=True)
    (s / "approvals").mkdir(parents=True, exist_ok=True)
    (s / "consumed").mkdir(parents=True, exist_ok=True)
    for keyname in ("operator_priv.hex", "audit_signer_priv.hex"):
        p = s / keyname
        if not p.exists():
            priv = Ed25519PrivateKey.generate()
            p.write_text(priv.private_bytes_raw().hex(), encoding="utf-8")
    op_priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex((s / "operator_priv.hex").read_text().strip()))
    (s / "operator_pub.hex").write_text(_raw_pub(op_priv).hex(), encoding="utf-8")


def _read_client(stand: str, *, with_worker: bool) -> UsabilityClient:
    s = Path(stand)
    op_pub = bytes.fromhex((s / "operator_pub.hex").read_text().strip())
    au_priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex((s / "audit_signer_priv.hex").read_text().strip()))
    idx = LocalIndex(str(s / "index"))
    ledger = AuditLedger(str(s / "audit.jsonl"), ledger_id="cli-L1",
                         signer_private=au_priv, signer_public_bytes=_raw_pub(au_priv))
    worker = None
    worker_identity = ""
    if with_worker:
        from agentnode_sdk.sandbox.container_backend import ContainerBackend
        from agentnode_sdk.worker.local import LocalWorker
        worker = LocalWorker(ContainerBackend())
        worker_identity = "host:" + worker.configuration_sha256()[:16]
    return UsabilityClient(index=idx, worker=worker, audit_ledger=ledger,
                           approval_store=ApprovalStore(consumed_dir=str(s / "consumed")),
                           operator_public_bytes=op_pub, worker_identity=worker_identity, stand=stand)


def _cmd_import_npm(args) -> int:
    from agentnode_sdk.capability import npm_import, npm_vendor
    _ensure_stand(args.stand)
    s = Path(args.stand)
    tree_root = s / "vendor" / f"{args.package.replace('/', '_')}@{args.version}"
    vt = npm_vendor.vendor_npm(args.package, args.version, tree_root, runtime=args.runtime,
                               source_url=args.source_url, license_=args.license,
                               publisher=args.publisher)
    op_priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex((s / "operator_priv.hex").read_text().strip()))
    imp = npm_import.import_vendored_tree(
        vt.tree_dir, publisher=args.publisher_namespace, name=args.name, version=args.version,
        entry=vt.entry, interpreter=vt.interpreter, private_key=op_priv,
        public_key_bytes=_raw_pub(op_priv), registry=vt.registry, timeout=args.timeout)
    idx = LocalIndex(str(s / "index"))
    stored = idx.put_blob(imp.tar_bytes)
    assert stored == imp.tar_sha256, "blob sha mismatch"
    resolved = idx.register(imp.manifest)
    (s / "capability_id.txt").write_text(resolved.capability_id, encoding="utf-8")
    print(json.dumps({"imported": resolved.capability_id, "version": resolved.capability_version,
                      "tar_sha256": imp.tar_sha256, "tree_digest": imp.tree_digest,
                      "entry": imp.entry, "tools": [t["name"] for t in imp.manifest["tools"]]},
                     indent=2))
    return 0


def _cmd_list(args) -> int:
    print(json.dumps(_read_client(args.stand, with_worker=False).list(), indent=2))
    return 0


def _cmd_search(args) -> int:
    print(json.dumps(_read_client(args.stand, with_worker=False).search(args.query), indent=2))
    return 0


def _cmd_describe(args) -> int:
    c = _read_client(args.stand, with_worker=args.with_worker)
    print(json.dumps(c.describe_full(args.capability_id, args.version), indent=2, default=str))
    return 0


def _cmd_config(args) -> int:
    stand = args.stand
    if args.client == "generic":
        print(json.dumps(client_config.generic_mcp_config(stand, name=args.name), indent=2))
    elif args.client == "claude-code":
        print(json.dumps(client_config.claude_code_config(stand, name=args.name), indent=2))
    elif args.client == "codex":
        print(client_config.codex_config_toml(stand, name=args.name))
    return 0


def _cmd_prepare(args) -> int:
    c = _read_client(args.stand, with_worker=False)
    arguments = json.loads(args.args)
    nonce = c.deterministic_nonce(*_idver(c, args), args.tool, arguments)
    inv = c.prepare(args.capability_id, args.tool, arguments, version=args.version, nonce=nonce)
    print(json.dumps({"invocation_hash": inv.invocation_hash, "binding": inv.binding(),
                      "approval_required": c.approval_required(inv).to_dict()}, indent=2, default=str))
    return 0


def _cmd_approve(args) -> int:
    s = Path(args.stand)
    op_priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex((s / "operator_priv.hex").read_text().strip()))
    cap_id = args.audience or (s / "capability_id.txt").read_text(encoding="utf-8").strip()
    ap = grant(args.invocation_hash, audience=cap_id, human_actor=args.actor,
               operator_private=op_priv, operator_public_bytes=_raw_pub(op_priv),
               ttl_seconds=args.ttl)
    out = s / "approvals" / f"{ap.approval_id}.json"
    out.write_text(json.dumps(ap.to_dict(), indent=2), encoding="utf-8")
    print(json.dumps({"deposited": str(out), "approval_id": ap.approval_id,
                      "invocation_hash": ap.invocation_hash, "audience": ap.audience,
                      "expires_at": ap.expires_at}, indent=2))
    return 0


def _cmd_run(args) -> int:
    c = _read_client(args.stand, with_worker=True)
    arguments = json.loads(args.args)
    nonce = c.deterministic_nonce(*_idver(c, args), args.tool, arguments)
    inv = c.prepare(args.capability_id, args.tool, arguments, version=args.version, nonce=nonce)
    c.approval_store.load_dir(str(Path(args.stand) / "approvals"))
    approval_id = c.approval_store.find_unconsumed_for(inv.invocation_hash)
    if approval_id is None:
        print(json.dumps(c.approval_required(inv).to_dict(), indent=2))
        return 3
    try:
        res = c.submit(inv, approval_id)
    except ApprovalError as exc:
        # a spent single-use approval (durably consumed, e.g. the same call run twice) refuses
        # cleanly here rather than surfacing a traceback -- step 7 of USABILITY.md.
        print(json.dumps({"status": "refused", "reason": str(exc),
                          "invocation_hash": inv.invocation_hash}, indent=2))
        return 4
    print(json.dumps(c.bound_result(inv, res), indent=2, default=str))
    return 0


def _idver(c: UsabilityClient, args):
    resolved = c.index.resolve(args.capability_id, args.version)
    return resolved.capability_id, resolved.capability_version


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agentnode-capability-usability")
    sub = p.add_subparsers(dest="cmd", required=True)

    ip = sub.add_parser("import-npm", help="vendor + import a real upstream npm MCP server")
    ip.add_argument("--stand", required=True)
    ip.add_argument("--package", required=True, help="npm package name, e.g. @modelcontextprotocol/server-sequential-thinking")
    ip.add_argument("--version", required=True)
    ip.add_argument("--publisher-namespace", required=True, help="capability publisher namespace, e.g. modelcontextprotocol")
    ip.add_argument("--name", required=True, help="capability name, e.g. server-sequential-thinking")
    ip.add_argument("--source-url", default="")
    ip.add_argument("--license", default="")
    ip.add_argument("--publisher", default="", help="upstream publisher (provenance)")
    ip.add_argument("--runtime", default="podman")
    ip.add_argument("--timeout", type=float, default=120.0)
    ip.set_defaults(fn=_cmd_import_npm)

    lp = sub.add_parser("list"); lp.add_argument("--stand", required=True); lp.set_defaults(fn=_cmd_list)
    srp = sub.add_parser("search"); srp.add_argument("--stand", required=True); srp.add_argument("query"); srp.set_defaults(fn=_cmd_search)
    dp = sub.add_parser("describe"); dp.add_argument("--stand", required=True); dp.add_argument("capability_id")
    dp.add_argument("--version"); dp.add_argument("--with-worker", action="store_true"); dp.set_defaults(fn=_cmd_describe)

    cp = sub.add_parser("config"); cp.add_argument("--stand", required=True)
    cp.add_argument("--client", choices=["generic", "claude-code", "codex"], default="generic")
    cp.add_argument("--name", default="agentnode-capability"); cp.set_defaults(fn=_cmd_config)

    pp = sub.add_parser("prepare"); pp.add_argument("--stand", required=True)
    pp.add_argument("capability_id"); pp.add_argument("tool"); pp.add_argument("--version")
    pp.add_argument("--args", default="{}"); pp.set_defaults(fn=_cmd_prepare)

    ap = sub.add_parser("approve"); ap.add_argument("--stand", required=True)
    ap.add_argument("--invocation-hash", required=True, dest="invocation_hash")
    ap.add_argument("--actor", required=True); ap.add_argument("--audience", default="")
    ap.add_argument("--ttl", type=float, default=300.0); ap.set_defaults(fn=_cmd_approve)

    rp = sub.add_parser("run"); rp.add_argument("--stand", required=True)
    rp.add_argument("capability_id"); rp.add_argument("tool"); rp.add_argument("--version")
    rp.add_argument("--args", default="{}"); rp.set_defaults(fn=_cmd_run)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
