"""The operator's door to the catalog. Never an MCP tool; it needs the operator private key.

  python -m agentnode_sdk.remote_mcp.catalog_cli --stand S init
  python -m agentnode_sdk.remote_mcp.catalog_cli --stand S publish --capability ID --version V --export TOOL
  python -m agentnode_sdk.remote_mcp.catalog_cli --stand S unpublish --tool NAME
  python -m agentnode_sdk.remote_mcp.catalog_cli --stand S set-default --tools A,B
  python -m agentnode_sdk.remote_mcp.catalog_cli --stand S set-account --account ACCT (--tools A,B | --empty | --clear)
  python -m agentnode_sdk.remote_mcp.catalog_cli --stand S rollback --to-generation N
  python -m agentnode_sdk.remote_mcp.catalog_cli --stand S show
  python -m agentnode_sdk.remote_mcp.catalog_cli --stand S heads

Every change is recorded in the catalog ledger (actor, command, old/new generation and digest).
Refusals print a structured {status: refused, code, detail} and exit 5.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentnode_sdk.remote_mcp.catalog import Catalog, CatalogRefused, CatalogUnavailable
from agentnode_sdk.remote_mcp.catalog_audit import CatalogLedger


def _key(stand: str, name: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(io.open(os.path.join(stand, name), encoding="utf-8").read().strip()))


def _pub(k: Ed25519PrivateKey) -> bytes:
    return k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _open(stand: str, *, writable: bool) -> tuple[Catalog, CatalogLedger]:
    au = _key(stand, "audit_signer_priv.hex")
    ledger = CatalogLedger(os.path.join(stand, "catalog-audit.jsonl"), signer_private=au,
                           signer_public_bytes=_pub(au))
    op_pub = bytes.fromhex(io.open(os.path.join(stand, "operator_pub.hex"), encoding="utf-8").read().strip())
    op = _key(stand, "operator_priv.hex") if writable else None
    catalog = Catalog(stand, op_pub, operator_private=op,
                      on_change=lambda ev: ledger.append("operator_change", **ev))
    return catalog, ledger


def _summary(snap) -> dict:
    return {"generation": snap.generation, "digest": snap.digest,
            "published": {t: e.to_dict() for t, e in sorted(snap.published.items())},
            "default_active": list(snap.default_active),
            "accounts": {a: list(t) for a, t in sorted(snap.accounts.items())}}


def _tools(value: str | None) -> list[str]:
    return [t for t in (value or "").split(",") if t]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agentnode-mcp-catalog")
    p.add_argument("--stand", required=True)
    p.add_argument("--actor", default=os.environ.get("USER") or os.environ.get("USERNAME") or "operator")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    pp = sub.add_parser("publish")
    pp.add_argument("--capability", required=True)
    pp.add_argument("--version", required=True)
    pp.add_argument("--export", required=True)
    up = sub.add_parser("unpublish")
    up.add_argument("--tool", required=True)
    dp = sub.add_parser("set-default")
    dp.add_argument("--tools", default="")
    ap = sub.add_parser("set-account")
    ap.add_argument("--account", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--tools")
    g.add_argument("--empty", action="store_true")
    g.add_argument("--clear", action="store_true")
    rp = sub.add_parser("rollback")
    rp.add_argument("--to-generation", type=int, required=True)
    sub.add_parser("show")
    sub.add_parser("heads")
    args = p.parse_args(argv)

    writable = args.cmd not in ("show", "heads")
    catalog, ledger = _open(args.stand, writable=writable)
    try:
        if args.cmd == "init":
            snap = catalog.init(actor=args.actor)
        elif args.cmd == "publish":
            snap = catalog.publish(args.capability, args.version, args.export, actor=args.actor)
        elif args.cmd == "unpublish":
            snap = catalog.unpublish(args.tool, actor=args.actor)
        elif args.cmd == "set-default":
            snap = catalog.set_default(_tools(args.tools), actor=args.actor)
        elif args.cmd == "set-account":
            tools = None if args.clear else ([] if args.empty else _tools(args.tools))
            snap = catalog.set_account(args.account, tools, actor=args.actor)
        elif args.cmd == "rollback":
            snap = catalog.rollback(args.to_generation, actor=args.actor)
        elif args.cmd == "heads":
            snap = catalog.load()
            print(json.dumps({"catalog": {"generation": snap.generation, "digest": snap.digest},
                              "catalog_audit": ledger.head(),
                              "catalog_audit_entries_verified": ledger.verify()}, indent=1))
            return 0
        else:
            snap = catalog.load()
    except CatalogRefused as exc:
        print(json.dumps({"status": "refused", "code": exc.code, "detail": exc.detail}, indent=1))
        return 5
    except CatalogUnavailable as exc:
        print(json.dumps({"status": "unavailable", "detail": str(exc)}, indent=1))
        return 6
    print(json.dumps(_summary(snap), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
