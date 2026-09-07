"""`agentnode gateway` -- running a sandbox other machines can send work to.

This is the operator's side. Someone sets a gateway up on a machine that has a container runtime,
and other people's agents send code to it to be run in isolation.

The wording here is deliberate. A person setting this up wants to know four things, and every
command answers them in this order:

* what is happening now,
* whether the sandbox is actually protecting anything,
* what was prevented, if something was,
* what to do next.

Words like policy digest, HMAC and conformance vantage do not appear. They are real and they
matter, but they belong in `--verbose` and in diagnostic output, not in the sentence that tells
someone their gateway is not ready. A person who has just installed this does not yet have the
vocabulary, and greeting them with it mostly teaches them that this tool is not for them.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from agentnode_sdk.cli.output import bold, dim


def _root(args) -> Path:
    """Where this gateway keeps its identity, tokens and measurements."""
    if getattr(args, "dir", None):
        return Path(args.dir)
    home = os.environ.get("AGENTNODE_HOME")
    return (Path(home) if home else Path.home() / ".agentnode") / "gateway"


def _config_path(root: Path) -> Path:
    return root / "config.json"


def _load_config(root: Path) -> dict:
    path = _config_path(root)
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _save_config(root: Path, config: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _config_path(root).write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")


def _service(root: Path):
    from agentnode_sdk.gateway.identity import GatewayState
    from agentnode_sdk.gateway.server import GatewayService
    from agentnode_sdk.sandbox.container_backend import ContainerBackend

    from agentnode_sdk import __version__ as version

    state = GatewayState(root, version=str(version))
    return state, GatewayService(state, backend=ContainerBackend())


def _tls_from(config: dict, args):
    from agentnode_sdk.gateway.transport import TlsFiles

    cert = getattr(args, "tls_cert", None) or config.get("tls_cert")
    key = getattr(args, "tls_key", None) or config.get("tls_key")
    if cert and key:
        return TlsFiles(certfile=str(cert), keyfile=str(key))
    return None


def _say_protected(readiness, runtime_available: bool) -> None:
    """The one thing an operator most needs to know, in one line."""
    if not runtime_available:
        print(f"  {bold('Not protecting anything yet')} -- there is no container runtime here, so")
        print("  no code can be isolated and none will be run.")
        return
    if readiness.ready:
        print(f"  {bold('Protected')} -- code sent here runs inside a container, as a user with no")
        print("  privileges, and is cleaned up afterwards. This has been measured, not assumed.")
    else:
        print(f"  {bold('Not protecting anything yet')} -- {readiness.reason}")


# ---------------------------------------------------------------------------


def cmd_init(args) -> int:
    root = _root(args)
    config = _load_config(root)

    cert = getattr(args, "tls_cert", None)
    key = getattr(args, "tls_key", None)
    if bool(cert) != bool(key):
        print("  A certificate needs its key. Pass both --tls-cert and --tls-key, or neither.")
        return 2
    if cert and key:
        from agentnode_sdk.gateway.transport import InsecureTransportError, TlsFiles

        try:
            TlsFiles(certfile=str(cert), keyfile=str(key)).context()
        except InsecureTransportError as exc:
            # Checked now rather than at start, so the failure happens while the person is still
            # looking at the files they just typed.
            print(f"  {exc}")
            return 1
        config["tls_cert"] = str(Path(cert).resolve())
        config["tls_key"] = str(Path(key).resolve())

    root.mkdir(parents=True, exist_ok=True)
    state, service = _service(root)
    identity = state.identity                       # generated on first read
    _save_config(root, config)

    print()
    print(f"  {bold('This machine is now a sandbox gateway.')}")
    print(f"  Its files are in {dim(str(root))}")
    print(f"  It calls itself {dim(identity.gateway_id[:12])}")
    print()
    if config.get("tls_cert"):
        print("  It will serve over an encrypted connection, so other machines can reach it.")
    else:
        print("  It will accept connections from this machine only. That is the safe default:")
        print("  without encryption, a pairing code and an access token would be readable by")
        print("  anyone on the network in between.")
    print()
    print("  Next:")
    print("    agentnode gateway doctor --measure   check what this machine can actually enforce")
    print("    agentnode gateway start              start accepting work")
    return 0


def cmd_start(args) -> int:
    from agentnode_sdk.gateway.server import make_server
    from agentnode_sdk.gateway.transport import InsecureTransportError, public_url_for

    root = _root(args)
    config = _load_config(root)
    state, service = _service(root)
    host = getattr(args, "host", None) or config.get("host") or "127.0.0.1"
    port = int(getattr(args, "port", None) or config.get("port") or 8099)

    try:
        server = make_server(service, host=host, port=port, tls=_tls_from(config, args))
    except InsecureTransportError as exc:
        print()
        print(f"  {bold('Not started.')}")
        print(f"  {exc}")
        return 1
    except OSError as exc:
        print(f"  Could not listen on {host}:{port} -- {exc}")
        print(f"  Something else may already be using that port. Try: "
              f"agentnode gateway start --port {port + 1}")
        return 1

    url = public_url_for(host, server.server_address[1], bool(server.agentnode_tls))
    readiness = service.readiness_now()
    available = service.backend.check_available().available

    print()
    print(f"  {bold('Sandbox gateway running')} at {url}")
    _say_protected(readiness, available)
    print()
    if not readiness.ready:
        print("  It will refuse work until then. Next:")
        for step in readiness.next_steps:
            print(f"    {step}")
    else:
        print("  To let someone connect, run this in another terminal:")
        print("    agentnode gateway pair")
    print()
    print(dim("  Press Ctrl-C to stop."))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
        print("  Stopped. Nothing is listening any more.")
    finally:
        server.server_close()
    return 0


def cmd_status(args) -> int:
    root = _root(args)
    if not _config_path(root).is_file() and not (root / "identity.json").is_file():
        print("  No gateway is set up here. Run: agentnode gateway init")
        return 1
    state, service = _service(root)
    availability = service.backend.check_available()
    readiness = service.readiness_now()
    clients = state.paired_clients()

    print()
    print(f"  {bold('Sandbox gateway')} {dim(state.identity.gateway_id[:12])}")
    _say_protected(readiness, availability.available)
    print()
    print(f"  Connected clients: {len(clients)}")
    for entry in clients:
        name = str(entry.get("client_name") or "unnamed")
        print(f"    {name}")
    if not readiness.ready:
        print()
        print("  Next:")
        for step in readiness.next_steps:
            print(f"    {step}")
    if getattr(args, "verbose", False):
        print()
        print(dim("  measured properties:"))
        for name, held in sorted(readiness.properties.items()):
            print(dim(f"    {name:<28} {held}"))
        print(dim(f"    measured_at {readiness.measured_at}"))
    return 0


def cmd_doctor(args) -> int:
    root = _root(args)
    state, service = _service(root)
    availability = service.backend.check_available()

    print()
    print(f"  {bold('Checking this machine')}")
    print()
    if not availability.available:
        print("  There is no usable container runtime here, so nothing can be isolated.")
        print(f"  {availability.reason}")
        print()
        print("  Install Docker or Podman, then run this again.")
        return 1
    print(f"  A container runtime is available ({availability.backend}).")

    if getattr(args, "measure", False):
        print("  Measuring what it actually enforces. This runs several short containers")
        print("  and takes a minute or two.")
        readiness = service.measure()
    else:
        readiness = service.readiness_now()

    print()
    _say_protected(readiness, availability.available)
    if readiness.unproven:
        from agentnode_sdk.gateway.readiness import describe_missing

        print()
        print(f"  Not shown: {describe_missing(readiness.unproven)}.")
        print("  Work that asks for those will be refused rather than run anyway.")
    if not readiness.ready:
        print()
        print("  Next:")
        for step in readiness.next_steps:
            print(f"    {step}")
        return 1

    _say_remote_access(root, _load_config(root))
    return 0


def _say_remote_access(root: Path, config: dict) -> None:
    """Whether anyone else can reach this, and what to do about it.

    A gateway on loopback is not a problem to be fixed -- it is the right answer for someone
    running it on their own machine. It is only worth raising because the person who wants a
    second machine to use it has no way to find out what to do next except by being told.
    """
    import shutil

    print()
    if config.get("tls_cert"):
        print(f"  {bold('Reachable from other machines')} over its own certificate.")
        return

    print(f"  {bold('This sandbox is reachable from this machine only.')}")
    print("  That is the safe default and is all you need if you are the only one using it.")
    print()
    print("  To let another machine use it, the connection has to be encrypted -- a pairing code")
    print("  and an access token cross it, and neither survives being read on the way.")
    print()
    if shutil.which("tailscale"):
        print("  Tailscale is installed here, which is the simplest route:")
        print("    tailscale serve --bg 8099")
        print("  That publishes an https:// address on your private network. Nothing is exposed")
        print("  to the internet, and there is no certificate for you to manage.")
    else:
        print("  The simplest route needs no domain name and no open port:")
        print("    install Tailscale (or another private tunnel), then:")
        print("      tailscale serve --bg 8099")
        print("  If you already run Caddy or nginx with a certificate, put it in front instead")
        print("  and leave this gateway on 127.0.0.1.")


def cmd_pair(args) -> int:
    root = _root(args)
    state, service = _service(root)
    readiness = service.readiness_now()
    if not readiness.ready:
        print()
        print(f"  {bold('No code issued.')} {readiness.reason}")
        print("  Pairing someone to a gateway that will refuse their work only wastes their time.")
        print()
        print("  Next:")
        for step in readiness.next_steps:
            print(f"    {step}")
        return 1

    code = state.start_pairing()
    print()
    print(f"  {bold('Give this code to the person connecting:')}")
    print()
    print(f"      {bold(code)}")
    print()
    print("  It works once and expires in 15 minutes.")
    print("  On their machine:")
    print(f"    agentnode remote connect <this gateway's address> --code {code}")
    print()
    print(dim("  Read it out or type it in. Do not paste it into a chat -- anyone who sees it"))
    print(dim("  before the person you meant can use it instead of them."))
    return 0


def cmd_clients(args) -> int:
    root = _root(args)
    state, _service_unused = _service(root)
    clients = state.paired_clients()
    print()
    if not clients:
        print("  Nobody is connected. Run `agentnode gateway pair` to let someone in.")
        return 0
    print(f"  {bold('Connected clients')}")
    print()
    for entry in clients:
        name = str(entry.get("client_name") or "unnamed")
        client_id = str(entry.get("client_id") or "")[:12]
        print(f"    {name:<24} {dim(client_id)}")
    print()
    print("  To disconnect one:  agentnode gateway revoke --client <id>")
    return 0


def _resolve_client(state, wanted: str) -> str:
    """Match on the client id or its name. Returns the token hash key, or ''."""
    tokens = state._read_tokens()                      # noqa: SLF001 - same package, one owner
    wanted = str(wanted or "").strip()
    for token_hash, entry in tokens.items():
        if str(entry.get("client_id", "")).startswith(wanted) and wanted:
            return token_hash
        if str(entry.get("client_name", "")) == wanted and wanted:
            return token_hash
    return ""


def cmd_revoke(args) -> int:
    root = _root(args)
    state, _unused = _service(root)
    key = _resolve_client(state, getattr(args, "client", ""))
    if not key:
        print(f"  No connected client matches {getattr(args, 'client', '')!r}.")
        print("  Run `agentnode gateway clients` to see the list.")
        return 1
    tokens = state._read_tokens()                      # noqa: SLF001
    name = str(tokens[key].get("client_name") or "unnamed")
    del tokens[key]
    state._write_tokens(tokens)                        # noqa: SLF001
    print()
    print(f"  {bold(name)} can no longer send work here, starting now.")
    print("  Anything it had already submitted keeps running; it just cannot ask about it.")
    print("  To let them back in, run `agentnode gateway pair` and give them a new code.")
    return 0


def dispatch(args) -> int:
    action = getattr(args, "gateway_command", None)
    handlers = {
        "init": cmd_init,
        "start": cmd_start,
        "status": cmd_status,
        "doctor": cmd_doctor,
        "pair": cmd_pair,
        "clients": cmd_clients,
        "revoke": cmd_revoke,
    }
    handler = handlers.get(action)
    if handler is None:
        print("  Usage: agentnode gateway {init|start|status|doctor|pair|clients|revoke}")
        return 2
    try:
        return handler(args)
    except KeyboardInterrupt:
        print()
        return 130
    except Exception as exc:                                  # noqa: BLE001
        # Deliberately not a traceback: this is an operator command, and the useful part is
        # what failed rather than where. --verbose is for the rest.
        print(f"  That did not work: {exc}")
        if getattr(args, "verbose", False):
            raise
        return 1
