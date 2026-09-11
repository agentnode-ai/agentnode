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


def _this_account() -> str:
    """The account this process is running as, for a message that tells an operator what to fix."""
    try:
        import getpass

        return getpass.getuser()
    except Exception:                                         # noqa: BLE001 - never worth failing
        import os

        return str(getattr(os, "getuid", lambda: "?")())


def cmd_serve(args) -> int:
    """Serve one socket, for one account, until something stops this process."""
    from agentnode_sdk.worker.service import CannotHoldItsLimits, serve

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
    print("  Before it opens the socket it hits a memory ceiling, to see whether one binds.")
    print("  state, no signing identity and no client's token.")
    try:
        serve(address, key, uid)
    except KeyboardInterrupt:                                 # pragma: no cover - operator
        print("\n  stopped.")
        return 0
    except CannotHoldItsLimits as refusal:
        # Told at length, because the operator has to change the deployment and the failure is
        # one that otherwise looks like success: the runtime is up, the flag is accepted, and
        # nothing applies it.
        print()
        print(f"  {bold('This worker will not serve: its limits do not bind.')}")
        print()
        print("  " + str(refusal.reason))
        print()
        print("  Nothing was opened and no job can reach this machine. That is deliberate: a")
        print("  worker whose ceilings are not applied runs foreign code with no ceiling at")
        print("  all, on a host that believes it has one.")
        print()
        if not refusal.evidence.get("isolation"):
            print("  With a rootless runtime this is usually one missing thing -- the account has")
            print("  no systemd user session, so the runtime falls back to cgroupfs and drops the")
            print("  limit. Give it one, then start the worker again:")
            # The account that needs a session is the one THIS process runs as -- the worker. Not
            # --for-user, which is the account allowed to send it jobs.
            print(f"    loginctl enable-linger {_this_account()}")
            print("  and check that the unit's XDG_RUNTIME_DIR is that session's directory.")
        return 1
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
