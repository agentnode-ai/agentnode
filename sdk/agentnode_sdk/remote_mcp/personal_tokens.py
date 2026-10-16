"""Human-operated mint/revoke of revocable personal/service tokens (issuance path B, R5-R6).

This is a HUMAN CLI, never an MCP tool and never reachable from a model/transport. It issues
through the same :class:`TokenStore` the OAuth AS uses, so the one verifier checks both. The
plaintext is shown exactly once; the store keeps only the keyed hash.

  python -m agentnode_sdk.remote_mcp.personal_tokens mint  --store S --key-file K \
      --principal <id> --audience https://mcp.agentnode.net/mcp --scope mcp:use --ttl 3600 [--label L] [--service]
  python -m agentnode_sdk.remote_mcp.personal_tokens revoke --store S --key-file K --token-id <id>
  python -m agentnode_sdk.remote_mcp.personal_tokens list   --store S --key-file K
"""
from __future__ import annotations

import argparse
import io
import os
import sys

from agentnode_sdk.remote_mcp.tokens import TokenStore


def _load_key(path: str) -> bytes:
    key = io.open(path, "rb").read().strip()
    if len(key) < 32:
        raise SystemExit("key-file must hold >= 32 bytes")
    return key


def _store(args) -> TokenStore:
    key = _load_key(args.key_file)
    if os.path.exists(args.store):
        return TokenStore.load_hashed(args.store, key)
    return TokenStore(key)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="personal_tokens")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("mint", "revoke", "list"):
        sp = sub.add_parser(name)
        sp.add_argument("--store", required=True)
        sp.add_argument("--key-file", required=True)
        if name == "mint":
            sp.add_argument("--principal", required=True,
                            help="the AgentNode account id this token acts as (e.g. acct-…)")
            sp.add_argument("--client-id", required=True,
                            help="the gateway device id this token is bound to; in production "
                                 "the device must stay paired and keep RUN or the token is refused")
            sp.add_argument("--audience", required=True)
            sp.add_argument("--scope", action="append", default=[])
            sp.add_argument("--ttl", type=int, required=True)
            sp.add_argument("--label", default=None)
            sp.add_argument("--service", action="store_true")
        if name == "revoke":
            sp.add_argument("--token-id", required=True)
    args = p.parse_args(argv)
    store = _store(args)

    if args.cmd == "mint":
        scopes = tuple(args.scope) or ("mcp:use",)
        issued = store.mint(principal_id=args.principal, audience=args.audience, scopes=scopes,
                            issuance_kind="service" if args.service else "personal",
                            ttl_seconds=args.ttl, label=args.label,
                            device_or_connection_id=args.client_id)
        store.dump_hashed(args.store)
        # the ONLY place the plaintext is ever shown; it is not logged
        sys.stdout.write("token_id: %s\n" % issued.record.token_id)
        sys.stdout.write("ACCESS TOKEN (shown once, store it now):\n%s\n" % issued.plaintext)
        return 0
    if args.cmd == "revoke":
        ok = store.revoke(args.token_id, reason="operator")
        store.dump_hashed(args.store)
        sys.stdout.write("revoked\n" if ok else "no such active token\n")
        return 0 if ok else 1
    if args.cmd == "list":
        for rec in sorted(store._by_id.values(), key=lambda r: r.issued_at):  # noqa: SLF001
            v = rec.public_view()
            sys.stdout.write("%s  principal=%s kind=%s scopes=%s exp=%s revoked=%s label=%s\n" % (
                v["token_id"], v["principal_id"], v["issuance_kind"], v["scopes"],
                v["expires_at"], v["revoked_at"], v["label"]))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
