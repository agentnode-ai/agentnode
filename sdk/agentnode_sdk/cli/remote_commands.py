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
import time
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
                                    fingerprint=saved.fingerprint,
                                    certificate_sha256=saved.certificate_sha256)


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

    from agentnode_sdk.gateway.invitation import NotAnInvitation, PREFIX
    from agentnode_sdk.gateway.invitation import read as an_invitation

    given = str(args.url).strip()
    code = str(getattr(args, "code", "") or "")
    expect = ""
    named_gateway = ""
    if given.startswith(PREFIX):
        from agentnode_sdk.gateway.invitation import details as what_it_carries

        try:
            carried = what_it_carries(given)
            given, code, expect = an_invitation(given)
        except NotAnInvitation as exc:
            print()
            print(f"  {bold('That invitation could not be used.')}")
            print(f"  {exc}")
            return 2
        named_gateway = str(carried.get("gateway", ""))
        # Said here, before anything is contacted. An expired invitation that fails at the far
        # end looks like a network problem, and people debug a network that is working.
        dies_at = float(carried.get("expires", 0) or 0)
        if dies_at and dies_at < time.time():
            ago = time.time() - dies_at
            print()
            print(f"  {bold('That invitation has expired.')}")
            print("  It stopped working %s ago, and nothing was contacted."
                  % ("%d minutes" % (ago // 60) if ago >= 60 else "%d seconds" % ago))
            print("  Invitations are short-lived on purpose: one that stayed valid would be a")
            print("  key that keeps working long after whoever was sent it has forgotten it.")
            print()
            print("  Ask for another:  agentnode gateway pair")
            return 2
    elif not code:
        print()
        print("  Paste the invitation you were given, or pass the address and --code:")
        print("    agentnode remote connect agentnode-invite-1....")
        print("    agentnode remote connect https://sandbox.example:8099 --code ABCD-EFGH-IJKL")
        return 2

    url = given.rstrip("/")
    try:
        hello = gc.hello(url, pin=expect)
        # The certificate is what makes the answer trustworthy; this is a separate question --
        # whether the gateway that answered is the one the invitation was written for. They can
        # differ when an old invitation is used against a gateway that has since been rebuilt,
        # and then pairing would appear to work and the client would be attached to something
        # nobody meant.
        if named_gateway:
            answered = str((hello.get("gateway") or {}).get("gateway_id", ""))
            if answered and answered != named_gateway:
                print()
                print(f"  {bold('That is not the gateway this invitation was written for.')}")
                print(f"  The invitation names {named_gateway[:12]}, and {url} calls itself")
                print(f"  {answered[:12]}. Nothing was paired.")
                print()
                print("  This usually means the gateway was rebuilt after the invitation was")
                print("  made. Ask for a new one.")
                return 1
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
        connection = gc.pair(url, code, client_name=getattr(args, "as_name", "") or "",
                             certificate_sha256=expect)
    except gc.GatewayClientError as exc:
        print()
        print(f"  {bold('That did not pair.')} {exc}")
        return 1

    name = getattr(args, "as_name", "") or _name_from(url)
    _store(args).save(SavedGateway(name=name, url=url, token=connection.token,
                                   gateway_id=connection.gateway_id,
                                   fingerprint=connection.fingerprint,
                                   certificate_sha256=connection.certificate_sha256))
    print()
    print(f"  {bold('Connected')} to the sandbox at {url}, saved as {bold(name)}.")
    print()
    _explain_protection(hello)
    print()
    print("  Your access is stored on this machine only, readable by you alone.")
    if expect:
        print("  This client will talk to that sandbox's certificate and to nothing else:")
        print(f"  {dim(expect)}")
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
        # WITH the certificate this client pinned when it paired. Asking without it was the one
        # request in the whole exchange that went to whatever answered -- and on a gateway with
        # a self-signed certificate it did not even get that far: ordinary CA verification
        # failed, and the failure was reported as "that is not the sandbox you paired with",
        # which is a different and much more alarming thing than "this request forgot to pin".
        hello = gc.hello(saved.url, pin=saved.certificate_sha256)
        # `hello` is unauthenticated -- it has to be, since it is what an unpaired client asks
        # first. But this connection already knows which gateway it paired with, and
        # EM3C-EXTERNAL-0011 found the answer being read out to the user without that comparison:
        # a substituted endpoint could have reported itself protected and measured. Anything this
        # command is about to repeat has to come from the gateway it belongs to.
        gc.assert_same_gateway(connection, hello)
    except gc.GatewayClientError as exc:
        print(f"  {bold('That is not the sandbox you paired with.')}")
        print(f"  {exc}")
        return 1
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
        # Read from the record rather than asserted from what was asked for. The two are not the
        # same thing, and saying the second while meaning the first is what C3 was about.
        eff = final.get("effective_policy") or {}
        if "network.enabled" in eff:
            print("  It had no network access."
                  if not eff.get("network.enabled") else "  It had network access.")
        cleaned = final.get("cleanup_verified")
        if cleaned is True:
            print("  It was removed afterwards, and that was confirmed.")
        elif cleaned is False:
            print("  It was NOT removed afterwards.")
        else:
            print("  Whether it was removed afterwards could not be confirmed.")
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
    # What follows is what is being ASKED for. What is granted is not known until the gateway
    # has composed the policy, and it can be narrower -- EM3C-EGRESS-CLASSIFY-0001 saw this line
    # promise a destination the run never got. The grant is printed below, from the answer.
    if allow:
        print(f"  Asking to reach: {', '.join(allow)} -- and nothing else.")
    else:
        print("  Asking for no network access.")

    try:
        answer = gc.submit(connection, artifact, network=network,
                           allowed_domains=allow,
                           wall_clock_s=int(getattr(args, "max_seconds", 0) or 60),
                           required_properties=("container_isolation",))
    except Exception as exc:                                  # noqa: BLE001
        print(f"  It would not take the job: {exc}")
        return 1
    if answer.get("state") == "refused":
        print()
        print(f"  {bold('Refused, and nothing was run.')}")
        print(f"  {answer.get('refusal')}")
        return 1

    # Printed so it can be stopped from another terminal. A job you cannot name is a job you
    # cannot cancel -- and flushed, because when this is piped anywhere the id would otherwise sit
    # in a buffer until the job ended, which is exactly when it stops being useful. The two-role
    # check found that by trying to read it the way a person would.
    print(f"  run: {answer['run_id']}", flush=True)

    # Now the grant can be stated, because the gateway has answered with what it composed.
    granted_net = (answer.get("effective_policy") or {})
    if "network.enabled" in granted_net:
        if not granted_net.get("network.enabled"):
            print("  Granted: no network access.")
        else:
            hosts = granted_net.get("network.allowed_destinations") or []
            print("  Granted: " + (", ".join(hosts) + " -- and nothing else."
                                   if hosts else "network access with no destination allowed."))

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

    from agentnode_sdk.gateway.protocol import TIMED_OUT, TIMEOUT_EXIT_STATUS

    state = final.get("state")
    # Whatever the answer said, and nothing where it said nothing. `EM3C-E8-RECORD-0001`: this
    # read the field "or exited", so a client could print a reason the gateway had never given.
    reason = str(final.get("termination_reason") or "")
    if reason == TIMED_OUT:
        # Read from what it MEANS, never from a number. `EM3C-E4-CLASSIFY-0001`: this returned
        # the gateway's exit code, which for a timeout was -1, which Windows then reported as
        # 4294967295 -- a status no caller could tell from an ordinary failure.
        native = final.get("native_status")
        where = final.get("native_platform") or "the sandbox"
        print()
        print(f"  {bold('It ran out of time.')} The sandbox stopped it at its limit.")
        if native is not None:
            print(f"  ({where} reported {native} for the stopped container.)")
        return TIMEOUT_EXIT_STATUS
    if state == "finished":
        code = final.get("exit_code")
        if code is None:
            print()
            print(f"  {bold('Did not finish.')} Nothing exited, and no reason was given.")
            return 1
        return int(code)
    if state == "unverified":
        print()
        print(f"  {bold('It ran, but not everything could be confirmed.')}")
        print(f"  {final.get('refusal')}")
        return 1
    print()
    # "cancelled" on its own reads as something the person did. When the operator's switch is
    # what ended it, saying so is the difference between a person looking at their code and a
    # person looking at the sandbox.
    halted = str(final.get("halted_by") or "")
    if halted:
        print(f"  {bold('Did not finish.')} The sandbox was stopped by whoever runs it:")
        print(f"  {halted}")
        print("  Nothing about your code is known from this -- it was ended part-way.")
        return 1
    print(f"  {bold('Did not finish.')} {final.get('refusal') or state}")
    return 1


def cmd_cancel(args) -> int:
    from agentnode_sdk.gateway import client as gc

    saved, connection = _connection(args)
    if saved is None:
        return _no_gateway()
    from agentnode_sdk.gateway.protocol import outcome_of

    try:
        record, settled = gc.cancel(connection, str(args.run))
    except Exception as exc:                                  # noqa: BLE001
        print(f"  Could not stop it: {exc}")
        return 1
    print()
    print(f"  Asked {bold(saved.name)} to stop {args.run}.")
    # Every word below comes out of the answer the gateway signed and this client verified, and
    # the outcome is derived from two of its signed fields rather than carried as a third.
    # `EM3C-E6-RECORD-0001`: what stood here was read off an unverified body, and it told somebody
    # a run was running after the gateway had destroyed its container.
    if not settled:
        print(f"  It has not stopped yet: the gateway waited, and it was still "
              f"{record.get('state')}.")
        print(f"  Ask again, or look:  agentnode remote status --run {args.run}")
        return 1
    outcome = outcome_of(str(record.get("state") or ""),
                         str(record.get("termination_reason") or ""))
    print(f"  It stopped. State: {record.get('state')} ({outcome}).")
    return 0


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


def cmd_verify(args) -> int:
    """The client half of the external run."""
    from agentnode_sdk.tools import external_check

    saved = _store(args).get(getattr(args, "name", "") or "")
    url = getattr(args, "gateway", "") or (saved.url if saved else "")
    if not url:
        print("  Give the gateway's address with --gateway, or connect to it first.")
        return 2
    if not getattr(args, "code", ""):
        print("  Give the pairing code with --code. The gateway prints one with")
        print("    agentnode gateway pair")
        return 2
    return external_check.main(["--role", "client", "--gateway", url, "--code", args.code])


def dispatch(args) -> int:
    action = getattr(args, "remote_command", None)
    handlers = {
        "connect": cmd_connect,
        "list": cmd_list,
        "use": cmd_use,
        "status": cmd_status,
        "test": cmd_test,
        "run": cmd_run,
        "cancel": cmd_cancel,
        "rotate": cmd_rotate,
        "disconnect": cmd_disconnect,
        "verify": cmd_verify,
    }
    handler = handlers.get(action)
    if handler is None:
        print("  Usage: agentnode remote "
              "{connect|list|use|status|test|run|cancel|rotate|disconnect|verify}")
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
