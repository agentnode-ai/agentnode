"""`agentnode worker` -- the account that runs foreign code, and nothing else.

Two commands and no more. This process holds no pairing state, no signing identity, no client's
token and no ledger; there is no subcommand here that names one, because there is nothing here to
name.

## What this is not

Running the worker under the same account as the gateway would make all of this decoration. The
point is that the account starting `worker serve` is the one that can drive a container runtime,
and the account running the gateway is not. On one host that is a separation of accounts and not
isolation -- they share a kernel, and an account that can drive a container runtime can usually
become root on the machine. `ALPHA-BOUNDARY-0001` decided where foreign code belongs; this is the
arrangement until it gets there.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from agentnode_sdk.cli.output import bold


def cmd_key(args) -> int:
    """Write the key the gateway and the worker authenticate messages with.

    One file, readable by both accounts and by nobody else. It is not a secret that protects the
    machine -- anything that is root here can read it, along with everything else -- it is what
    makes the protocol the same when the worker moves to a machine where that is not true.
    """
    from agentnode_sdk.worker import protocol as wire

    # The string, not the path: `Path("")` is the current directory, which exists -- so asking
    # the path whether it is empty would answer a different question than the one being asked.
    named = str(getattr(args, "at", "") or "").strip()
    if not named:
        print()
        print("  Where should it go? Pass --at <path>.")
        return 2
    at = Path(named)
    if at.exists() and not getattr(args, "force", False):
        print()
        print(f"  There is already a key at {at}. Replacing it would stop every gateway that")
        print("  holds the old one from being able to say anything to this worker.")
        print("  To replace it anyway:  agentnode worker key --at <path> --force")
        return 1
    at.parent.mkdir(parents=True, exist_ok=True)
    # Written with the permissions it needs from the first instant it exists, rather than written
    # and then narrowed -- there is no moment in which it is readable by anyone else.
    handle = os.open(str(at), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    with os.fdopen(handle, "wb") as fh:
        fh.write(wire.new_key())
    print()
    print(f"  A key for this gateway and its worker is at {at}.")
    print("  Give it to both accounts and to nobody else:")
    print(f"    chown <worker-account>:<shared-group> {at} && chmod 640 {at}")
    return 0


def cmd_serve(args) -> int:
    """Serve one socket, for one account, until something stops this process."""
    from agentnode_sdk.worker.service import serve

    address = str(getattr(args, "socket", "") or "")
    key = str(getattr(args, "key", "") or "")
    for_whom = getattr(args, "for_user", None)
    if not address or not key or not for_whom:
        print()
        print("  A worker needs to know where to listen, what to authenticate messages with,")
        print("  and which account may speak to it:")
        print("    agentnode worker serve --socket unix:///run/agentnode/worker.sock \\")
        print("                           --key /etc/agentnode/worker.key \\")
        print("                           --for-user agentnode-gateway")
        return 2
    try:
        uid = int(for_whom)
    except ValueError:
        import pwd

        try:
            uid = pwd.getpwnam(str(for_whom)).pw_uid
        except KeyError:
            print()
            print(f"  There is no account called {for_whom} on this machine, so there is nobody")
            print("  to serve. A worker is started for one account and refuses to guess.")
            return 1

    print()
    print(f"  {bold('AgentNode sandbox worker')}")
    print("  This account is the only one that drives a container runtime. It holds no pairing")
    print("  state, no signing identity and no client's token.")
    try:
        serve(address, key, uid)
    except KeyboardInterrupt:                                 # pragma: no cover - operator
        print("\n  stopped.")
        return 0
    except Exception as exc:                                  # noqa: BLE001
        print(f"  It did not start: {exc}")
        return 1
    return 0


def dispatch(args) -> int:
    action = getattr(args, "worker_command", None)
    handlers = {"key": cmd_key, "serve": cmd_serve}
    if action not in handlers:
        print()
        print("  agentnode worker key   --at <path>")
        print("  agentnode worker serve --socket <unix://...> --key <path> --for-user <account>")
        return 2
    return handlers[action](args)


def add_parser(subparsers) -> None:
    """The `worker` verb, in the one place the CLI's shape is decided."""
    worker = subparsers.add_parser(
        "worker", help="Run the account that executes sandboxed code")
    actions = worker.add_subparsers(dest="worker_command")

    key = actions.add_parser("key", help="Make the key the gateway and this worker share")
    key.add_argument("--at", default="", metavar="PATH")
    key.add_argument("--force", action="store_true",
                     help="Replace a key that is already there, and stop every gateway holding "
                          "the old one from reaching this worker")

    serve = actions.add_parser("serve", help="Listen on a socket for one account")
    serve.add_argument("--socket", default="", metavar="ADDRESS",
                       help="unix:///run/agentnode/worker.sock")
    serve.add_argument("--key", default="", metavar="PATH")
    serve.add_argument("--for-user", dest="for_user", default=None, metavar="ACCOUNT",
                       help="the account the gateway runs as; nothing else may speak here")


__all__ = ["add_parser", "dispatch", "cmd_key", "cmd_serve", "sys"]
