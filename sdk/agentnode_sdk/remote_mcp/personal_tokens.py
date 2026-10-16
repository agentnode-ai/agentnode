"""Human-operated mint/revoke of revocable personal/service tokens (issuance path B, R5-R6).

This is a HUMAN CLI, never an MCP tool and never reachable from a model/transport. It issues
through the same :class:`TokenStore` the OAuth AS uses, so the one verifier checks both. The
plaintext is shown exactly once; the store keeps only the keyed hash.

  python -m agentnode_sdk.remote_mcp.personal_tokens mint  --store S --key-file K \
      --principal <id> --audience https://mcp.agentnode.net/mcp --scope mcp:use --ttl 3600 [--label L] [--service]
  python -m agentnode_sdk.remote_mcp.personal_tokens revoke --store S --key-file K --token-id <id>
  python -m agentnode_sdk.remote_mcp.personal_tokens revoke-principal --store S --key-file K --principal <id>
  python -m agentnode_sdk.remote_mcp.personal_tokens list   --store S --key-file K

`revoke` works for ANY token id: a personal/service token held in the store file is revoked
there; an OAuth token (held only by the running service, listed from the service's hash-free
`tokens.oauth.public.json` next to the store) is tombstoned in the store file. Either way the
running service refuses it from its next request (it follows the store file).
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
    for name in ("mint", "revoke", "revoke-principal", "list"):
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
        if name == "revoke-principal":
            sp.add_argument("--principal", required=True)
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
        if store.get(args.token_id) is not None:
            ok = store.revoke(args.token_id, reason="operator")
            store.dump_hashed(args.store)
            sys.stdout.write("revoked\n" if ok else "already revoked\n")
            return 0 if ok else 1
        # not held in the file: an OAuth token of the running service -> tombstone it
        store.record_revocation(args.token_id)
        store.dump_hashed(args.store)
        sys.stdout.write("revocation recorded; the running service refuses it from its next request\n")
        return 0
    if args.cmd == "revoke-principal":
        gen = store.revoke_all_for(args.principal)
        store.dump_hashed(args.store)
        sys.stdout.write("all tokens of %s revoked (generation %d)\n" % (args.principal, gen))
        return 0
    if args.cmd == "list":
        views = [rec.public_view() for rec in store._by_id.values()]  # noqa: SLF001
        public = os.path.join(os.path.dirname(os.path.abspath(args.store)), "tokens.oauth.public.json")
        if os.path.exists(public):
            import json
            with io.open(public, encoding="utf-8") as fh:
                views += json.load(fh).get("oauth_tokens", [])
        tomb = store._revoked_ids  # noqa: SLF001
        for v in sorted(views, key=lambda r: r["issued_at"]):
            revoked = v["revoked_at"] or tomb.get(v["token_id"])
            sys.stdout.write("%s  principal=%s kind=%s scopes=%s exp=%s revoked=%s label=%s\n" % (
                v["token_id"], v["principal_id"], v["issuance_kind"], v["scopes"],
                v["expires_at"], revoked, v["label"]))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
