"""A thin CLI over the one CapabilityClient contract (search / list / describe / prepare).

The CLI, the SDK (CapabilityClient) and the MCP adapter all resolve through the
same ``CapabilityClient`` and ``contract.prepare`` -- so a CLI ``prepare`` of a
tool yields the same canonical ``invocation_hash`` as the SDK and the adapter for
the same inputs. Actually running a capability still goes through
``prepare -> operator approval -> submit`` (an operator approval the CLI user
obtains out of band); the CLI never self-approves.
"""
from __future__ import annotations

import argparse
import json
import sys

from agentnode_sdk.capability.client import CapabilityClient
from agentnode_sdk.capability.index import LocalIndex


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agentnode-capability")
    p.add_argument("--index", required=True, help="local capability index root")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("search"); sp.add_argument("query")
    sub.add_parser("list")
    dp = sub.add_parser("describe"); dp.add_argument("capability_id"); dp.add_argument("--version")
    pp = sub.add_parser("prepare")
    pp.add_argument("capability_id"); pp.add_argument("tool")
    pp.add_argument("--version"); pp.add_argument("--args", default="{}")
    args = p.parse_args(argv)

    client = CapabilityClient(index=LocalIndex(args.index))
    if args.cmd == "search":
        print(json.dumps(client.search(args.query), indent=2))
    elif args.cmd == "list":
        print(json.dumps(client.list(), indent=2))
    elif args.cmd == "describe":
        print(json.dumps(client.describe(args.capability_id, args.version), indent=2))
    elif args.cmd == "prepare":
        arguments = json.loads(args.args)
        resolved = client.index.resolve(args.capability_id, args.version)
        nonce = client.deterministic_nonce(resolved.capability_id, resolved.capability_version,
                                           args.tool, arguments)
        inv = client.prepare(args.capability_id, args.tool, arguments,
                             version=args.version, nonce=nonce)
        print(json.dumps({"invocation_hash": inv.invocation_hash, "binding": inv.binding()},
                        indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
