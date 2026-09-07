"""`agentnode remote` -- sending work to a sandbox on another machine.

This is the user's side of a gateway. They have a script, or an agent that produces one, and they
want it to run somewhere that is not their laptop and cannot touch their files.

Nothing here asks anyone to edit JSON, copy a digest, or read a URL out of a config file. The
whole ordinary path is:

    agentnode remote connect https://sandbox.example.com --code ABCD-EFGH-JKLM
    agentnode remote test
    agentnode remote run ./script.py

Everything else -- which gateway is selected, where the token is kept, what was checked -- is the
tool's job to remember and the tool's job to explain when it matters.
"""
from __future__ import annotations

import sys
from pathlib import Path

from agentnode_sdk.cli.output import bold, dim


def _store(args):
    from agentnode_sdk.gateway.connections import ConnectionStore

    return ConnectionStore(getattr(args, "store", None) or None)


def _connection(args):
    """The selected gateway, as something the client library can use."""
    from agentnode_sdk.gateway.client import GatewayConnection

    saved = _store(args).get(getattr(args, "name", "") or "")
    if saved is None:
        return None, None
    return saved, GatewayConnection(base_url=saved.url, token=saved.token,
                                    gateway_id=saved.gateway_id,
                                    fingerprint=saved.fingerprint)


def _no_gateway() -> int:
    print()
    print("  No sandbox is connected yet.")
    print("  Ask whoever runs it for a pairing code, then:")
    print("    agentnode remote connect <address> --code <code>")
    return 1


def _explain_protection(hello: dict) -> None:
    if hello.get("ready"):
        print("  Your code will run inside a container on that machine, as a user with no")
        print("  privileges, and will be cleaned up afterwards. The gateway has measured this.")
    else:
        print(f"  {bold('That sandbox is not ready')} -- {hello.get('reason') or 'it did not say why'}")
        print("  It will refuse work until whoever runs it fixes that.")


# ---------------------------------------------------------------------------


def cmd_connect(args) -> int:
    from agentnode_sdk.gateway import client as gc
    from agentnode_sdk.gateway.connections import SavedGateway
    from agentnode_sdk.gateway.transport import (
        CredentialInUrlError,
        InsecureTransportError,
    )

    url = str(args.url).rstrip("/")
    try:
        hello = gc.hello(url)
    except InsecureTransportError as exc:
        print()
        print(f"  {bold('Did not connect.')}")
        print(f"  {exc}")
        return 1
    except CredentialInUrlError as exc:
        print(f"  {exc}")
        return 2
    except gc.GatewayClientError as exc:
        print(f"  Could not reach a sandbox gateway at {url}: {exc}")
        print("  Check the address, and that it is running.")
        return 1

    try:
        connection = gc.pair(url, str(args.code), client_name=getattr(args, "as_name", "") or "")
    except gc.GatewayClientError as exc:
        print()
        print(f"  {bold('That did not pair.')} {exc}")
        return 1

    name = getattr(args, "as_name", "") or _name_from(url)
    _store(args).save(SavedGateway(name=name, url=url, token=connection.token,
                                   gateway_id=connection.gateway_id,
                                   fingerprint=connection.fingerprint))
    print()
    print(f"  {bold('Connected')} to the sandbox at {url}, saved as {bold(name)}.")
    print()
    _explain_protection(hello)
    print()
    print("  Your access is stored on this machine only, readable by you alone.")
    print("  Next:  agentnode remote test")
    return 0


def _name_from(url: str) -> str:
    from urllib.parse import urlparse

    host = urlparse(url).hostname or "sandbox"
    return host.replace(".", "-")


def cmd_list(args) -> int:
    store = _store(args)
    names = store.names()
    if not names:
        return _no_gateway()
    default = store.default_name()
    print()
    print(f"  {bold('Sandboxes you can send work to')}")
    print()
    for name in names:
        saved = store.get(name)
        mark = "*" if name == default else " "
        print(f"   {mark} {name:<20} {dim(saved.url if saved else '')}")
    print()
    print(dim("  * is the one used when you do not name another."))
    return 0


def cmd_use(args) -> int:
    store = _store(args)
    if not store.set_default(str(args.name)):
        print(f"  There is no connected sandbox called {args.name!r}.")
        print("  Run `agentnode remote list` to see them.")
        return 1
    print(f"  Work now goes to {bold(str(args.name))} unless you say otherwise.")
    return 0


def cmd_status(args) -> int:
    from agentnode_sdk.gateway import client as gc

    saved, connection = _connection(args)
    if saved is None:
        return _no_gateway()
    print()
    print(f"  {bold(saved.name)}  {dim(saved.url)}")
    try:
        hello = gc.hello(saved.url)
    except Exception as exc:                                  # noqa: BLE001
        print(f"  Cannot reach it right now: {exc}")
        print("  Your access is still saved; try again when it is back.")
        return 1
    _explain_protection(hello)
    if hello.get("next_steps"):
        print()
        print("  Whoever runs it needs to:")
        for step in hello["next_steps"]:
            print(f"    {step}")
    if getattr(args, "verbose", False):
        print()
        print(dim(f"    gateway id      {hello.get('gateway', {}).get('gateway_id', '')}"))
        print(dim(f"    fingerprint     {hello.get('fingerprint', '')}"))
        print(dim(f"    protocol        {hello.get('protocol', '')}"))
        for name, held in sorted((hello.get("properties") or {}).items()):
            print(dim(f"    {name:<28} {held}"))
    return 0


def cmd_test(args) -> int:
    """Prove the whole path works, with a job small enough to read."""
    from agentnode_sdk.gateway import client as gc

    saved, connection = _connection(args)
    if saved is None:
        return _no_gateway()

    print()
    print(f"  Sending a tiny test program to {bold(saved.name)}.")
    artifact = b"print('the sandbox ran this')\n"
    try:
        answer = gc.submit(connection, artifact, network="none",
                           required_properties=("container_isolation",))
    except Exception as exc:                                  # noqa: BLE001
        print(f"  It would not take the job: {exc}")
        return 1
    if answer.get("state") == "refused":
        print()
        print(f"  {bold('Refused')} -- {answer.get('refusal')}")
        return 1
    try:
        final = gc.wait_for(connection, answer["run_id"], timeout=180)
    except Exception as exc:                                  # noqa: BLE001
        print(f"  The job did not come back: {exc}")
        return 1

    print()
    if final.get("state") == "finished":
        print(f"  {bold('It works.')}")
        print(f"  The sandbox ran the program and sent back: {(final.get('stdout') or '').strip()!r}")
        print("  It had no network access and was removed afterwards.")
        return 0
    if final.get("state") == "unverified":
        print(f"  {bold('It ran, but not everything could be confirmed.')}")
        print(f"  {final.get('refusal')}")
        return 1
    print(f"  {bold('It did not finish.')} {final.get('refusal') or final.get('state')}")
    return 1


def cmd_run(args) -> int:
    from agentnode_sdk.gateway import client as gc

    saved, connection = _connection(args)
    if saved is None:
        return _no_gateway()
    path = Path(str(args.file))
    if not path.is_file():
        print(f"  There is no file at {path}.")
        return 2
    artifact = path.read_bytes()

    allow = tuple(getattr(args, "allow", None) or ())
    network = "restricted" if allow else "none"
    print()
    print(f"  Sending {bold(path.name)} to {bold(saved.name)}.")
    if allow:
        print(f"  It may reach: {', '.join(allow)} -- and nothing else.")
    else:
        print("  It has no network access.")

    try:
        answer = gc.submit(connection, artifact, network=network,
                           allowed_domains=allow,
                           required_properties=("container_isolation",))
    except Exception as exc:                                  # noqa: BLE001
        print(f"  It would not take the job: {exc}")
        return 1
    if answer.get("state") == "refused":
        print()
        print(f"  {bold('Refused, and nothing was run.')}")
        print(f"  {answer.get('refusal')}")
        return 1

    try:
        final = gc.wait_for(connection, answer["run_id"],
                            timeout=float(getattr(args, "timeout", 0) or 600))
    except Exception as exc:                                  # noqa: BLE001
        print(f"  The job did not come back: {exc}")
        return 1

    out = final.get("stdout") or ""
    err = final.get("stderr") or ""
    if out:
        sys.stdout.write(out if out.endswith("\n") else out + "\n")
    if err:
        sys.stderr.write(err if err.endswith("\n") else err + "\n")

    deltas = final.get("policy_deltas") or []
    if deltas:
        print()
        print("  The sandbox was stricter than asked:")
        for delta in deltas:
            print(f"    {delta.get('field')}: asked {delta.get('requested')!r}, "
                  f"got {delta.get('effective')!r}")

    state = final.get("state")
    if state == "finished":
        return int(final.get("exit_code") or 0)
    if state == "unverified":
        print()
        print(f"  {bold('It ran, but not everything could be confirmed.')}")
        print(f"  {final.get('refusal')}")
        return 1
    print()
    print(f"  {bold('Did not finish.')} {final.get('refusal') or state}")
    return 1


def cmd_rotate(args) -> int:
    from agentnode_sdk.gateway import client as gc
    from agentnode_sdk.gateway.connections import SavedGateway

    saved, connection = _connection(args)
    if saved is None:
        return _no_gateway()
    try:
        rotated = gc.rotate(connection)
    except Exception as exc:                                  # noqa: BLE001
        print(f"  Could not replace your access: {exc}")
        return 1
    _store(args).save(SavedGateway(name=saved.name, url=saved.url, token=rotated.token,
                                   gateway_id=rotated.gateway_id,
                                   fingerprint=rotated.fingerprint))
    print(f"  Your access to {bold(saved.name)} was replaced. The old one no longer works.")
    return 0


def cmd_disconnect(args) -> int:
    store = _store(args)
    name = getattr(args, "name", "") or store.default_name()
    if not name or not store.forget(name):
        print(f"  There is no connected sandbox called {name!r}.")
        return 1
    print()
    print(f"  Forgot {bold(name)} on this machine.")
    print("  The gateway still lists you until whoever runs it revokes you there:")
    print("    agentnode gateway revoke --client <id>")
    return 0


def dispatch(args) -> int:
    action = getattr(args, "remote_command", None)
    handlers = {
        "connect": cmd_connect,
        "list": cmd_list,
        "use": cmd_use,
        "status": cmd_status,
        "test": cmd_test,
        "run": cmd_run,
        "rotate": cmd_rotate,
        "disconnect": cmd_disconnect,
    }
    handler = handlers.get(action)
    if handler is None:
        print("  Usage: agentnode remote "
              "{connect|list|use|status|test|run|rotate|disconnect}")
        return 2
    try:
        return handler(args)
    except KeyboardInterrupt:
        print()
        return 130
    except Exception as exc:                                  # noqa: BLE001
        print(f"  That did not work: {exc}")
        if getattr(args, "verbose", False):
            raise
        return 1
