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
import time
from pathlib import Path

from agentnode_sdk.cli.output import bold, dim


def _root(args) -> Path:
    """Where this gateway keeps its identity, tokens and measurements."""
    if getattr(args, "dir", None):
        return Path(args.dir)
    home = os.environ.get("AGENTNODE_HOME")
    return (Path(home) if home else Path.home() / ".agentnode") / "gateway"


#: The one command that closes every refusal here. A gate that names no way through is a
#: wall, so the remediation is a command that really runs and really changes the answer.
_MEASURE_CMD = "agentnode gateway doctor --measure"


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


def _operator_policy(root: Path):
    """The ceiling this machine's owner set, or None to keep the built-in default.

    EM3C-EGRESS-CLASSIFY-0001 found the gateway had a deliberate "the operator opens it"
    default with no way for an operator to open it: `gateway start` never passed a policy, so
    the no-network default was the only reachable setting and `remote run --allow` could not
    be granted by any published command. This reads the setting the operator saved.

    Returning None rather than an all-denying policy matters: it keeps the default in ONE
    place, in the service, instead of restating it here where the two could drift apart.
    """
    allowed = _load_config(root).get("egress_allowed")
    if not allowed:
        return None

    from agentnode_sdk.sandbox.contract import NetworkRules, SandboxPolicy
    from agentnode_sdk.sandbox.egress import validate_allowed_domains

    hosts = tuple(str(h) for h in allowed)
    # Validated on the way in AND here, because a config file can be edited by hand between
    # the two. An unenforceable ceiling must not become a running gateway.
    validate_allowed_domains(hosts)
    return SandboxPolicy(network=NetworkRules(enabled=True,
                                              allowed_destinations=frozenset(hosts)))


def _service(root: Path):
    from agentnode_sdk.gateway.identity import GatewayState
    from agentnode_sdk.gateway.server import GatewayService
    from agentnode_sdk.sandbox.container_backend import ContainerBackend

    from agentnode_sdk import __version__ as version

    state = GatewayState(root, version=str(version))
    return state, GatewayService(state, backend=ContainerBackend(),
                                 operator_policy=_operator_policy(root))


def _tls_from(config: dict, args):
    from agentnode_sdk.gateway.transport import TlsFiles

    cert = getattr(args, "tls_cert", None) or config.get("tls_cert")
    key = getattr(args, "tls_key", None) or config.get("tls_key")
    if not cert and not key:
        # What `gateway init --tls-self-signed` left in this gateway's own directory. Found
        # rather than configured, so that an operator who made one does not also have to say
        # where it is -- and so that a gateway with one never serves in the clear by omission.
        root = _root(args)
        made, its_key = root / "tls-cert.pem", root / "tls-key.pem"
        if made.exists() and its_key.exists():
            cert, key = str(made), str(its_key)
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
    if getattr(args, "tls_self_signed", False):
        if cert or key:
            print("  Either this gateway makes a certificate for itself, or you give it one.")
            return 2
        advertise = str(getattr(args, "advertise", "") or "").strip()
        if not advertise:
            print()
            print("  What address will people connect to? That name or address goes in the")
            print("  certificate and in every invitation this gateway issues:")
            print("    agentnode gateway init --tls-self-signed --advertise sandbox.example")
            return 2
        from agentnode_sdk.gateway import certificate as tls

        root.mkdir(parents=True, exist_ok=True)
        # A port here is the easy mistake, and it used to be a silent one: the certificate was
        # made for a name with a colon in it, every invitation carried that name, and the first
        # sign of trouble was on somebody else's machine, where the client refused an address it
        # could not parse. The port is not this gateway's to advertise -- it is in the address
        # the invitation builds -- so say so now rather than at the far end.
        if ":" in advertise and not advertise.count(":") > 1:      # not an IPv6 literal
            host, _, tail = advertise.partition(":")
            print()
            print(f"  {bold('An address here, without a port.')}")
            print(f"  The port is added when an invitation is made, so {advertise!r} would put")
            print(f"  a colon into the certificate and into every invitation, and the client at")
            print("  the other end would refuse it. What you probably want:")
            print(f"    agentnode gateway init --tls-self-signed --advertise {host}")
            if tail and tail != "8099":
                print(f"  and then start it with  --port {tail}")
            return 2

        cert, key, pin = tls.make(root, advertise)
        config["advertise"] = advertise
        print()
        print(f"  {bold('A certificate for this gateway, made by this gateway.')}")
        print(f"  Clients pin it when they pair, so nothing else answering at {advertise}")
        print("  can take their place. No certificate authority is involved and none is needed:")
        print("  the invitation is what says which certificate to expect.")
        print(f"  {dim(pin)}")

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


def _refuse_unless_pinned(root, what: str) -> int:
    """0 when this service is what its pin says, 1 after saying why not.

    Called before anything is opened or served. The alpha ran its gateway on python 3.14 while
    CI tested 3.10-3.12, and nothing noticed, because nothing was looking. This is the thing
    that looks -- and it looks BEFORE a port exists, so a refusal leaves nothing half-started.

    A machine with no pin is allowed to start and is TOLD. That is deliberate: refusing there
    would break every installation that predates this check, which would be a new kind of
    outage in the name of preventing one. A pin that exists and disagrees is refused.
    """
    from agentnode_sdk.gateway import runtime_pin

    try:
        said = runtime_pin.check(root)
    except runtime_pin.NoPinAtAll:
        print()
        print(f"  {bold('No runtime pin.')}")
        print(f"  This {what} cannot say which interpreter and artefact it was meant to run")
        print("  from, so it cannot notice if it is running from the wrong one. It is starting")
        print("  anyway, because refusing here would stop installations made before this check")
        print("  existed. Write one with the deployment script to close that.")
        print(f"  Running on python {runtime_pin.running_python()}.")
        return 0
    except runtime_pin.NotWhatWasPinned as no:
        print()
        print(f"  {bold('Not started.')}")
        print(f"  This {what} is not what it was pinned to be, and the difference is the")
        print(f"  {no.which}.")
        print(f"  {no.said}")
        print(f"  {no.what_to_do}")
        return 1
    print(f"  Running as {said.get('build_id', '(no build id)')} "
          f"on python {runtime_pin.running_python()}.")
    return 0


def cmd_start(args) -> int:
    from agentnode_sdk.gateway.server import make_server
    from agentnode_sdk.gateway.transport import InsecureTransportError, public_url_for

    root = _root(args)
    # The PIN directory, not the state directory. A restore replaces the state; it must not be
    # able to replace what this installation is allowed to run as.
    from agentnode_sdk.gateway import runtime_pin as _rp

    if _refuse_unless_pinned(_rp.pin_dir(), "gateway"):
        return 1
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
    available = service.worker.can_it_isolate().available

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
        # Everything this command owns, given back in order and by name. The state holds a
        # directory descriptor and the server holds a listening socket and two watcher threads;
        # a finalizer exists for the ones nobody remembers, but it is a net and not the way
        # things are meant to end. `shutdown()` before `server_close()` because that is what
        # tells the watchers to stop -- closing the socket does not.
        server.shutdown()
        server.server_close()
        # The pool that carries out cancellations is owned by the service, so it is given back
        # here too. Production closes what it opens; the finalizer stays a net.
        service.close()
        state.close()
    return 0


def _egress_show(root, verbose: bool = False) -> int:
    """What is actually in force, and whether it matches what is configured."""
    from agentnode_sdk.gateway import operator_policy as opol
    from agentnode_sdk.gateway.activation import ActivationStore, SnapshotUnusable

    print()
    try:
        state = ActivationStore(root).load_active()
    except SnapshotUnusable as exc:
        print(f"  This gateway's active state cannot be trusted: {exc}")
        print("  Nothing runs until it has been measured again.")
        print(f"  Next:  {_MEASURE_CMD}")
        return 1

    try:
        configured = opol.from_config(_load_config(root))
    except opol.OperatorPolicyError as exc:
        print(f"  The saved policy cannot be read: {exc}")
        return 1

    if state is None:
        print("  Nothing is in force yet: this gateway has not been measured.")
        print("  Jobs reach nothing until it has been.")
        print(f"  Next:  {_MEASURE_CMD}")
        return 1

    active = state.policy
    if active.mode == opol.RESTRICTED:
        print("  Jobs on this gateway may reach:")
        for host in active.allowed_destinations:
            print(f"    {host}")
        print()
        print("  A job still has to ask for a host, and may ask for fewer than these.")
        print("  It can never be granted one that is not on this list.")
    else:
        print("  Jobs on this gateway reach nothing. No network at all.")
        print("  To allow a host:  agentnode gateway egress --allow example.com")

    if configured.digest() != state.policy_digest:
        print()
        print("  A different policy is saved than the one in force. The saved one has not been")
        print("  measured, so it is not being enforced and nothing will run under it.")
        print(f"  Next:  {_MEASURE_CMD}")
        return 1

    if verbose:
        print()
        # The mode belongs in the diagnostic block for the same reason the digests do: the prose
        # above says what a job may reach, and prose is what a reader has to interpret. External
        # evidence has to record the policy's own word for its mode rather than derive it from a
        # sentence that could be reworded (`EM3C-EVIDENCE-0003`).
        print(f"  network mode          : {active.mode}")
        print(f"  activation generation : {state.generation}")
        print(f"  policy digest         : {state.policy_digest}")
        print(f"  configured digest     : {configured.digest()}")
        print(f"  digests agree         : {configured.digest() == state.policy_digest}")
        print(f"  measured properties   : {', '.join(active.required_properties)}")
    return 0


def cmd_egress(args) -> int:
    """Show or set what jobs on this gateway may reach.

    Setting is one operation for the person typing it and a transaction underneath
    (`EM3C-Y6-DECISION-0001`): the proposal is saved as pending, the protections that proposal
    needs are measured against it, and only a complete measurement puts it into force. Nothing
    here says the change is saved, active or protecting anything until that has happened -- the
    whole finding this answers was a command stating a grant it did not have.
    """
    root = _root(args)
    allow = tuple(getattr(args, "allow", None) or ())
    clear = bool(getattr(args, "none", False))
    verbose = bool(getattr(args, "verbose", False))

    if allow and clear:
        print()
        print("  --allow and --none ask for opposite things. Pick one.")
        return 2

    if not allow and not clear:
        return _egress_show(root, verbose)

    from agentnode_sdk.gateway import operator_policy as opol

    try:
        proposed = (opol.build(opol.RESTRICTED, allow) if allow else opol.build(opol.NONE))
    except opol.OperatorPolicyError as exc:
        print()
        print(f"  That cannot be enforced as an allowlist: {exc}")
        print("  Nothing was changed.")
        return 2

    print()
    print("  Proposed policy saved as pending. The current policy is still the one in force.")
    print("  Measuring the protections this policy needs before anything changes:")
    for name in proposed.required_properties:
        print(f"    {name}")
    print()

    # The whole change -- recording the intent, measuring it, and putting it in force or putting
    # the previous one back -- happens inside the gateway's own activation lock, against this
    # exact proposal. EM3C-FINAL-0001 found this command writing the config file before the lock
    # was taken and restoring it after the lock was released, so two operators changing the
    # policy at once could measure one proposal and activate another.
    from agentnode_sdk.gateway.activation import ActivationStranded

    _state, service = _service(root)
    try:
        verdict = service.activate(proposed)
    except ActivationStranded as exc:
        # The one failure that does not leave the previous policy usable. Saying "nothing was
        # changed" here would be false, which is what EM3C-FINAL-0005 found being said.
        print()
        print(f"  {bold('This gateway needs measuring again before it will run anything.')}")
        print(f"  {exc}")
        print(f"  Next:  {_MEASURE_CMD}")
        return 1
    except Exception as exc:                                      # noqa: BLE001
        print(f"  The measurement could not be run: {exc}")
        # Accurate because the change commits at a single rename this path never reached, and
        # because a rename that failed after the generation was advanced puts that advance back.
        # EM3C-FINAL-0003 found this sentence printed where the snapshot HAD been replaced;
        # EM3C-FINAL-0005 found it printed where the previous snapshot had been left behind the
        # anchor. Both of those paths now say something else.
        print("  The previous policy remains in force. Nothing was changed.")
        return 1

    if not verdict.ready:
        print(f"  Measurement failed: {verdict.reason}")
        if verdict.unproven:
            print("  Not established:")
            for name in verdict.unproven:
                print(f"    {name}")
        print()
        print("  The previous policy remains in force. Nothing was changed.")
        return 1

    print("  Measurements passed. The new policy is now in force.")
    return _egress_show(root, verbose)


def cmd_status(args) -> int:
    root = _root(args)
    if not _config_path(root).is_file() and not (root / "identity.json").is_file():
        print("  No gateway is set up here. Run: agentnode gateway init")
        return 1
    state, service = _service(root)
    availability = service.worker.can_it_isolate()
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
    availability = service.worker.can_it_isolate()

    print()
    print(f"  {bold('Checking this machine')}")
    print()
    if not availability.available:
        print("  There is no usable container runtime for this gateway to send work to,")
        print("  so nothing can be isolated.")
        print(f"  {availability.reason}")
        print()
        if getattr(service.worker, "address", ""):
            print(f"  This gateway does not run containers itself. Its worker is at")
            print(f"    {service.worker.address}")
            print("  so that is the machine to look at, not this one.")
        else:
            print("  Install Docker or Podman, then run this again.")
        return 1
    where = getattr(service.worker, "address", "") or "in this process"
    print(f"  A container runtime is available ({availability.backend}), {where}.")

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
    # This gateway can make its own certificate, and a client pins it when it pairs. That is
    # fewer moving parts than anything below and needs nothing installed, so it goes first --
    # it used to be missing here entirely, and the advice led with "install Tailscale" for a
    # job the gateway already does.
    print("  This gateway can make its own certificate, and the invitation tells the client")
    print("  which one to expect -- so no certificate authority is involved and nothing else")
    print("  answering at that address can take its place:")
    print("    agentnode gateway init --tls-self-signed --advertise <the address people reach>")
    print("    agentnode gateway start --host 0.0.0.0")
    print("  Then open the port, deliberately, to the people who should have it.")
    print()
    print("  The alternatives, if you would rather not open one:")
    if shutil.which("tailscale"):
        print("  Tailscale is installed here:")
        print("    tailscale serve --bg 8099")
        print("  That publishes an https:// address on your private network. Nothing is exposed")
        print("  to the internet, and there is no certificate for you to manage.")
    else:
        print("    install Tailscale (or another private tunnel), then:")
        print("      tailscale serve --bg 8099")
        print()
        print("  If you would rather use a reverse proxy you already run, leave the gateway on")
        print("  127.0.0.1 and give Caddy this, replacing the name with your own:")
        print()
        print("      sandbox.example.com {")
        print("          reverse_proxy 127.0.0.1:8099")
        print("      }")
        print()
        print("  Caddy handles the certificate for you -- that is Caddy doing it, not AgentNode:")
        print("  this gateway never obtains or renews a certificate itself and has no plans to.")
        print("  For nginx, use proxy_pass http://127.0.0.1:8099; inside a server block that")
        print("  already terminates TLS. Either way the gateway keeps its default address.")
        print()
        print(dim("  These commands have not been run by this build. They are the shapes that"))
        print(dim("  work; your own network is what decides whether they do."))


def cmd_pair(args) -> int:
    root = _root(args)
    state, service = _service(root)

    if getattr(args, "withdraw", False):
        # An invitation is handed over out of band -- read aloud, pasted into a chat,
        # photographed off a screen -- and any of those can reach further than intended. Issuing
        # another one replaces it, but an operator who wants the outstanding one dead should not
        # have to create a live one to do it.
        print()
        if state.withdraw_pairing():
            print(f"  {bold('That code will not work now.')}")
            print("  Anyone still holding it gets the same answer as somebody holding a guess.")
        else:
            print("  There was no code outstanding, so there was nothing to take back.")
            return 1
        return 0
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

    import time as _clock

    from agentnode_sdk.gateway.identity import PAIRING_TTL_SECONDS

    # An invitation FOR an existing customer is how somebody adds a second machine. Named
    # here, by the operator, and never by whoever redeems it: a redeemer who could name the
    # account would be able to walk into one by guessing its name.
    joining = str(getattr(args, "account", "") or "").strip()
    if joining:
        known = {a.account_id for a in state.accounts.all()}
        known |= {str(d.get("account_id") or "") for d in state.paired_clients()}
        if joining not in known:
            print()
            print(f"  No account here is called {joining!r}.")
            print("  Run `agentnode gateway accounts` to see them. Leave --account out and this")
            print("  invitation makes a new customer.")
            return 1
    code = state.start_pairing(for_account=joining)
    dies_at = _clock.time() + PAIRING_TTL_SECONDS
    config = _load_config(root)
    where, pin = _where_and_what_to_expect(root, config, args)
    if where and pin:
        from agentnode_sdk.gateway.invitation import write as an_invitation

        print()
        print(f"  {bold('Give this to the person connecting:')}")
        print()
        print("      " + an_invitation(where, code, pin, expires=dies_at,
                                       gateway_id=state.identity.gateway_id))
        print()
        print("  It carries the address, the code, which certificate to expect, when it stops")
        print("  working and which gateway it is for -- so their client can tell this sandbox")
        print("  from anything else answering there, and can say that it has expired without")
        print("  having to try. It works once and expires in %d minutes."
              % (PAIRING_TTL_SECONDS // 60))
        print("  On their machine:")
        print("    agentnode remote connect <paste it here>")
        print()
        print(dim("  Hand it over the way you would a key. Anyone who sees it before the person"))
        print(dim("  you meant can pair as them -- and if that happens, or you simply change"))
        print(dim("  your mind:  agentnode gateway pair --withdraw"))
        return 0

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
    if not pin:
        print()
        print(dim("  This gateway has no certificate, so there is nothing for their client to"))
        print(dim("  pin and it can only be reached from this machine. To change that:"))
        print(dim("    agentnode gateway init --tls-self-signed --advertise <address>"))
    return 0


def _where_and_what_to_expect(root, config, args):
    """The address to put in an invitation and the certificate a client should expect.

    Both come from what this gateway was configured with, not from what is running: a code is
    issued by a different process from the one that serves, and asking the running one would mean
    an operator could not hand out an invitation before starting it.
    """
    from pathlib import Path as _Path

    cert = str(config.get("tls_cert") or "")
    if not cert:
        candidate = root / "tls-cert.pem"
        cert = str(candidate) if candidate.exists() else ""
    if not cert or not _Path(cert).exists():
        return "", ""
    from agentnode_sdk.gateway import certificate as tls

    try:
        pin = tls.fingerprint(_Path(cert).read_bytes())
    except Exception:                                         # noqa: BLE001
        return "", ""
    advertise = str(getattr(args, "advertise", "") or config.get("advertise") or "").strip()
    if not advertise:
        return "", pin
    port = int(getattr(args, "port", None) or config.get("port") or 8099)
    host = "[" + advertise + "]" if ":" in advertise and not advertise.startswith("[") else advertise
    return "https://%s:%d" % (host, port), pin


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
    from agentnode_sdk.gateway import accounts as _accounts

    for entry in clients:
        name = str(entry.get("client_name") or "unnamed")
        client_id = str(entry.get("client_id") or "")[:12]
        belongs = str(entry.get("account_id")
                      or _accounts.solo_account_for(str(entry.get("client_id") or "")))
        print(f"    {name:<24} {dim(client_id)}  {dim(belongs)}")
    print()
    print("  The third column is the CUSTOMER. Devices in one account can see each other;")
    print("  devices in different accounts cannot see each other at all.")
    print("  To disconnect one:  agentnode gateway revoke --client <id>")
    return 0


def cmd_keeps(args) -> int:
    """What this gateway keeps, for how long, and what expiring costs. Show or set.

    Every class has a period. An earlier version had two, because two files persisted by default
    and the rest expired on schedules nobody chose -- which is a retention period the operator
    cannot see, not an absence of one.
    """
    from agentnode_sdk.gateway import retention

    root = _root(args)
    asked = {name: getattr(args, "%s_days" % name, None) for name in retention.CLASSES}
    if all(value is None for value in asked.values()):
        try:
            now = retention.read_retention(root)
        except retention.RetentionUnreadable as unreadable:
            print()
            print(f"  {bold('This gateway is sweeping nothing.')}")
            print(f"  {unreadable}")
            return 1
        print()
        print(f"  {bold('What this gateway keeps')}")
        print()
        for row in retention.describe():
            days = now.days_for(row["name"])
            for_how_long = ("%d days" % days) if days else bold("indefinitely")
            print(f"    {row['name']:<12} {for_how_long:<22} {row['file']}")
            print(f"      {dim(row['is'])}")
            print(f"      {dim('when it expires: ' + row['expiring_means'])}")
            if row["also_expires_on_its_own"]:
                print(f"      {dim('also expires on its own, sooner; this is the ceiling')}")
        print()
        print("  A job's code and a job's output are in NO class here: they are never written")
        print("  to disk. They are held in memory for the run and handed back to whoever ran it.")

        # What the last attempt could NOT do. A sweep that failed on a class used to look
        # exactly like one that had nothing to do, and the operator is the only one who can fix
        # a store that has gone unwritable.
        last = retention.last_sweep(root)
        if last.get("problems"):
            print()
            print(f"  {bold('The last sweep could not finish.')}")
            for problem in last["problems"]:
                print(f"    {problem}")
            print("  It is still owed, and this gateway will try again on its next tick.")
        elif last.get("at"):
            print()
            print(f"  {dim('Last clean sweep: ' + time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(last['at'])))}")
        else:
            print()
            print(f"  {dim('This gateway has not swept yet.')}")
        print()
        print("  To change one:")
        print("    agentnode gateway keeps --audit-days 30")
        print("    agentnode gateway keeps --metering-days 0      (0 = indefinitely, on purpose)")
        return 0

    try:
        now = retention.read_retention(root)
    except retention.RetentionUnreadable:
        now = retention.Retention()
    changed = retention.Retention(**{
        **now.as_dict(),
        **{"%s_days" % name: int(value) for name, value in asked.items() if value is not None},
    })
    retention.write_retention(root, changed)
    print()
    print(f"  {bold('Set.')} It applies at the next sweep, which is at most an hour away.")
    for row in retention.describe():
        days = changed.days_for(row["name"])
        print(f"    {row['name']:<12} {('%d days' % days) if days else 'indefinitely'}")
    return 0


def cmd_sweep(args) -> int:
    """Sweep now rather than waiting for the hour. What an operator does after lowering one."""
    from agentnode_sdk.gateway import retention

    root = _root(args)
    try:
        done = retention.sweep(root)
    except retention.RetentionUnreadable as unreadable:
        print()
        print(f"  {bold('Nothing was swept.')}")
        print(f"  {unreadable}")
        return 1
    print()
    print(f"  {bold('Swept.')}")
    for name, how_many in sorted(done["swept"].items()):
        print(f"    {name:<12}: {how_many}")
    if done["problems"]:
        print()
        print(f"  {bold('Some of it could not be done:')}")
        for problem in done["problems"]:
            print(f"    - {problem}")
        return 1
    return 0


def cmd_export(args) -> int:
    """Hand one customer everything this gateway holds about them, and write down that you did.

    An OPERATOR command. There is no contract operation for this and there is deliberately no
    address: an export is everything about a person in one file, and the authority to produce one
    is "can log in to the machine that holds it" rather than "holds a capability".
    """
    from agentnode_sdk.gateway import retention

    root = _root(args)
    state, _service_unused = _service(root)
    wanted = str(getattr(args, "account", "") or "").strip()
    if not wanted:
        print()
        print("  Which account? Run `agentnode gateway accounts` to see them.")
        return 2
    known = {str(d.get("account_id") or "") for d in state.paired_clients()}
    known |= {a.account_id for a in state.accounts.all()}
    if wanted not in known:
        print()
        print(f"  No account here is called {wanted!r}.")
        return 1

    import json as _json

    body = _json.dumps(retention.export_account(state, wanted), indent=2, sort_keys=True) + "\n"
    where = str(getattr(args, "to", "") or "").strip() or ("%s-export.json" % wanted)
    with open(where, "w", encoding="utf-8") as fh:
        fh.write(body)
    try:
        os.chmod(where, 0o600)
    except OSError:
        pass
    retention.note_an_export(root, wanted, by="operator", how_many_bytes=len(body))

    print()
    print(f"  {bold('Written to ' + where)}  ({len(body)} bytes, readable only by you)")
    print("  It carries no credential, in any form, and nothing belonging to another account.")
    print()
    print("  This is RECORDED: exports.jsonl in the gateway's directory now says an export of")
    print("  this account was taken, when, and by whom. That is the only way to answer 'who has")
    print("  a copy of this' later.")
    print()
    print(dim("  A copy taken now survives a deletion made later. Say so when you hand it over."))
    return 0


def cmd_delete(args) -> int:
    """Remove a customer from this gateway, and say what that cannot reach."""
    from agentnode_sdk.gateway import retention

    root = _root(args)
    state, _service_unused = _service(root)
    wanted = str(getattr(args, "account", "") or "").strip()
    if not wanted:
        print()
        print("  Which account? Run `agentnode gateway accounts` to see them.")
        return 2
    if not getattr(args, "yes", False):
        print()
        print(f"  This removes {bold(wanted)} from this gateway: its devices, sessions,")
        print("  enrolments, counters, ledger entries and audit lines, and it ERASES its")
        print("  metering lines to signed tombstones.")
        print()
        print("  Run it again with --yes if that is what you want.")
        return 2

    went = retention.delete_account(state, wanted, because="the operator deleted this account")
    print()
    if not went.get("complete", True):
        print(f"  {bold('THIS DELETION DID NOT COMPLETE.')} Some of this account's data is still")
        print("  on this gateway:")
        for problem in went.get("problems", []):
            print(f"    - {problem}")
        print()
        print("  Fix what is named above and run this again. Do NOT tell the customer their")
        print("  data is gone until this says it is.")
        print()
    else:
        print(f"  {bold(wanted)} is gone from this gateway.")
    for name, how_many in sorted(went.items()):
        if name in ("problems", "complete"):
            continue
        print(f"    {name:<18}: {how_many}")
    print()
    print(f"  {bold('What this did NOT reach, and cannot:')}")
    # HOW MANY WERE HANDED OUT, from what the deletion itself removed. Read out of
    # `exports.jsonl` afterwards it is now always nought -- the deletion takes those lines with
    # it, because each one names the account and leaving them behind left the identifier in a
    # file nobody was looking at. The number still has to be SAID: somebody deleting an account
    # needs to know that copies of it are in other people's hands, and that is exactly the fact
    # the record was keeping.
    taken = int(went.get("export_records") or 0)
    print("    backups taken before now. They contain this account and this deletion cannot")
    print("      change a file it does not have. Retire them on their own schedule.")
    if taken:
        print("    %d export(s) of this account have been handed out (this gateway's record of"
              % taken)
        print("      when and to whom has just been removed with the rest of the account).")
        print("      A copy somebody holds is theirs to delete.")
    else:
        print("    no exports of this account were ever taken from this gateway.")
    return 0


def cmd_accounts(args) -> int:
    """List the customers on this gateway, and suspend or restore one.

    An operator command and only an operator command. There is no contract operation that does
    any of this, so no capability any customer can hold reaches it, over any door -- which is
    what keeps "the operator is not an account" true by construction rather than by a check
    somebody has to remember to write.
    """
    from agentnode_sdk.gateway import accounts as _accounts

    root = _root(args)
    state, _service_unused = _service(root)
    wanted = str(getattr(args, "account", "") or "").strip()
    because = str(getattr(args, "reason", "") or "").strip()

    devices = {}
    for entry in state.paired_clients():
        belongs = str(entry.get("account_id")
                      or _accounts.solo_account_for(str(entry.get("client_id") or "")))
        devices.setdefault(belongs, []).append(entry)

    if getattr(args, "suspend", False) or getattr(args, "restore", False):
        if not wanted:
            print()
            print("  Which account? Run `agentnode gateway accounts` to see them.")
            return 2
        if wanted not in devices and not state.accounts.recorded(wanted):
            print()
            print(f"  No account here is called {wanted!r}.")
            return 1
        if getattr(args, "restore", False):
            state.accounts.restore(wanted)
            print()
            print(f"  {bold(wanted)} can send work again.")
            return 0
        if not because:
            print()
            print("  Why? Whatever you say here is what that customer is shown:")
            print("    agentnode gateway accounts --account %s --suspend" % wanted)
            print('      --reason "repeated attempts to reach hosts we do not allow"')
            return 2
        state.accounts.suspend(wanted, because, by="operator")
        print()
        print(f"  {bold(wanted)} is suspended. Their next job is refused with your words.")
        print("  Runs already going are NOT stopped by this -- that is what the stop is for:")
        print("    agentnode gateway stop --reason ...")
        print("  To let them work again:")
        print(f"    agentnode gateway accounts --account {wanted} --restore")
        return 0

    from agentnode_sdk.gateway import accounts as _acc

    if getattr(args, "claim", False):
        if not wanted or not wanted.startswith(_acc.SOLO_PREFIX):
            print()
            print("  --claim turns a device that predates accounts into a named customer.")
            print("  Name it with the solo: id the list shows, and give it a name:")
            print('    agentnode gateway accounts --account solo:abcd... --claim --name "Acme"')
            return 2
        called = str(getattr(args, "name", "") or "").strip()
        if not called:
            print()
            print("  What is this customer called? A named account is the point of claiming one.")
            return 2
        if wanted not in devices:
            print()
            print(f"  No device here is in {wanted!r}.")
            return 1
        made = state.accounts.create(name=called)
        moved = 0
        for entry in devices[wanted]:
            if state.move_device_to(str(entry.get("client_id") or ""), made.account_id):
                moved += 1
        print()
        print(f"  {bold(called)} is now a customer: {made.account_id}")
        print(f"  {moved} device(s) moved into it, and nothing else changed -- the same")
        print("  credentials keep working, and their runs are still theirs.")
        print()
        print("  To add another machine to them, they can do it themselves from the console,")
        print("  or you can:  agentnode gateway pair --account %s" % made.account_id)
        return 0

    print()
    if not devices:
        print("  No customers yet. Run `agentnode gateway pair` to let the first one in.")
        return 0

    named = {a: d for a, d in devices.items() if not a.startswith(_acc.SOLO_PREFIX)}
    solo = {a: d for a, d in devices.items() if a.startswith(_acc.SOLO_PREFIX)}

    if named:
        print(f"  {bold('Customers')}")
        print()
        for account_id in sorted(named):
            try:
                found = state.accounts.get(account_id)
                standing = ("active" if found.active
                            else "suspended: " + found.suspended_because)
                called = found.name or "(unnamed)"
            except (_acc.NoSuchAccount, _acc.AccountsUnreadable) as exc:
                standing, called = "cannot be read (%s)" % str(exc)[:50], "?"
            print(f"    {called:<22} {dim(account_id)}  "
                  f"{len(named[account_id])} device(s)  {standing}")
            for entry in named[account_id]:
                print(f"      {dim(str(entry.get('client_name') or 'unnamed'))}")
        print()

    if solo:
        print(f"  {bold('Devices that predate accounts -- NOT customers yet')}")
        print()
        print("  Each of these was paired before this gateway had accounts, so each is its own")
        print("  account: the safe reading, and not a customer model. Several of them may")
        print("  belong to ONE person, and this gateway has no way to know which.")
        print()
        for account_id in sorted(solo):
            for entry in solo[account_id]:
                print(f"    {str(entry.get('client_name') or 'unnamed'):<22} "
                      f"{dim(account_id)}")
        print()
        print("  Turn one into a named customer -- their credential keeps working:")
        print('    agentnode gateway accounts --account <solo:id> --claim --name "Their name"')
        print()
    print()
    print("  To stop one:     agentnode gateway accounts --account <id> --suspend --reason ...")
    print("  To let them back: agentnode gateway accounts --account <id> --restore")
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


def cmd_verify(args) -> int:
    """The gateway half of the external run.

    A single command, because the person running it is checking whether this works at all and
    should not also be assembling one.
    """
    from agentnode_sdk.tools import external_check

    return external_check.main(["--role", "gateway"])


def binding_for_the_client(state, ledger, run_id: str, token: str) -> dict | None:
    """The binding this gateway wrote down for one run, for the client that submitted that run.

    `EM3C-CROSSING-0001`, F-C5-CROSS-CLIENT-READ: holding a run id was enough, whoever was
    holding it. The rule lives here, in one function, rather than inside the command -- so that
    anything else which has to answer this question asks the same rule instead of writing its own.

    None covers both "no such run" and "not yours". Separating them would let anybody holding a
    token learn which run ids are real, which is the thing the check is for.
    """
    asking = state.client_id_for(token)
    owner = str((ledger.run_entry(run_id) or {}).get("owner_client_id") or "")
    if not asking or not owner or asking != owner:
        return None
    return ledger.challenge_for(run_id)


def cmd_challenge(args) -> int:
    """What this gateway wrote down about the challenge it issued for ONE run.

    Read-only in the strongest sense available: it opens the ledger, takes one entry, prints it,
    and writes nothing. `EM3C-CROSSING-DECISION-0001`, F-A-READ-SURFACE -- so it answers about the
    run it is asked about and about nothing else. There is no listing and no way to ask for every
    run.

    And it answers to the client that SUBMITTED that run. `EM3C-CROSSING-0001`,
    F-C5-CROSS-CLIENT-READ: holding a run id used to be enough, whoever was holding it, so one
    client could read what this gateway wrote down about another client's work. Who is asking
    arrives on standard input, never as an argument, because a credential on a command line is one
    anybody listing processes can read. A run belonging to somebody else is answered exactly like
    a run that does not exist -- same words, same status -- because telling those apart would let
    anybody holding a token learn which run ids are real.

    What it CANNOT print is the challenge itself. The value is not in the ledger: only its digest
    was ever written there, and the value is dropped when the run ends. That is what makes this a
    second channel rather than a second copy of the first -- somebody holding this output cannot
    produce the value, they can only be told whether a value they already hold is the right one.
    """
    import json
    import sys as _sys

    from agentnode_sdk.gateway.identity import GatewayState
    from agentnode_sdk.gateway.ledger import Ledger

    from agentnode_sdk import __version__ as version

    root = _root(args)
    run_id = str(getattr(args, "run", "") or "")
    if not run_id:
        print()
        print("  Which run? Pass --run <id>. This answers about one run and never lists them.")
        return 2

    # Who is asking. `EM3C-CROSSING-0001`, F-C5-CROSS-CLIENT-READ: holding a run id was enough to
    # read that run's binding, so anybody who could run this command could read about a run that
    # was not theirs. The token arrives on STANDARD INPUT and never as an argument -- a credential
    # on a command line is one anybody listing processes can read, which is the same objection
    # that put the challenge on stdin.
    token = (_sys.stdin.read() if not _sys.stdin.isatty() else "").strip()
    if not token:
        print()
        print("  Who is asking? This answers to the client that submitted the run. Send its")
        print("  token on standard input -- never as an argument.")
        return 2

    binding = binding_for_the_client(
        GatewayState(root, version=str(version)), Ledger(root / "ledger.json"), run_id, token)
    if binding is None:
        print()
        print(f"  This gateway has nothing written down for a run {run_id}.")
        return 1
    print(json.dumps(binding, sort_keys=True, indent=2))
    return 0


def cmd_stop(args) -> int:
    """Stop taking work, at once, until somebody lifts it deliberately."""
    from agentnode_sdk.gateway.allowance import stop_everything

    reason = str(getattr(args, "reason", "") or "").strip()
    if not reason:
        print()
        print("  Why? Whatever you say here is what every client is told:")
        print('    agentnode gateway stop --reason "upgrading the sandbox image"')
        return 2
    at = stop_everything(_root(args), reason)
    print()
    print(f"  {bold('This gateway is not taking work.')}")
    print("  Every job sent to it is refused with what you just said, and every run that was")
    print("  going has been ended -- the reason to stop a gateway at once is usually the code")
    print("  running on it right now, and a switch that left it running would be no switch.")
    print("  The gateway acts on this within a second or so; it is a file, and this command and")
    print("  the gateway are different processes.")
    print(f"  {dim(str(at))}")
    print()
    print("  To take work again:  agentnode gateway resume")
    return 0


def cmd_resume(args) -> int:
    """Take work again. As deliberate as stopping was."""
    from agentnode_sdk.gateway.allowance import start_again

    if not start_again(_root(args)):
        print()
        print("  This gateway was not stopped, so there was nothing to lift.")
        return 1
    print()
    print(f"  {bold('Taking work again.')}")
    return 0


def cmd_limits(args) -> int:
    """Show or set what one client may use."""
    from agentnode_sdk.gateway.allowance import Allowance, read_allowance, write_allowance

    root = _root(args)
    now = read_allowance(root)
    asked = {name: getattr(args, name, None) for name in
             ("concurrent_runs", "runs_per_window", "seconds_per_window",
              "account_concurrent_runs", "account_runs_per_window",
              "account_seconds_per_window", "requests_per_minute",
              "account_requests_per_minute", "max_artifact_bytes", "max_output_bytes")}
    if all(value is None for value in asked.values()):
        print()
        print(f"  {bold('What one device may use')}")
        for name in ("concurrent_runs", "runs_per_window", "seconds_per_window"):
            value = now.as_dict()[name]
            print(f"    {name:<28}: {value if value else 'no limit'}")
        print()
        print(f"  {bold('What one CUSTOMER may use, across every device they have')}")
        for name in ("account_concurrent_runs", "account_runs_per_window",
                     "account_seconds_per_window"):
            value = now.as_dict()[name]
            print(f"    {name:<28}: {value if value else 'no limit'}")
        print()
        print(f"  {bold('How fast, and how big')}")
        for name in ("requests_per_minute", "account_requests_per_minute",
                     "max_artifact_bytes", "max_output_bytes"):
            value = now.as_dict()[name]
            print(f"    {name:<28}: {value if value else 'no limit'}")
        print(f"    {'window':<28}: {now.window_seconds / 3600:.0f} hours")
        print()
        print("  Both apply and the tighter one decides. A per-device ceiling alone is one a")
        print("  customer raises by pairing another machine, which is not a ceiling.")
        print()
        print("  To change one:")
        print("    agentnode gateway limits --runs-per-window 200")
        print("    agentnode gateway limits --account-runs-per-window 500")
        return 0
    changed = Allowance(**{**now.as_dict(),
                           **{k: int(v) for k, v in asked.items() if v is not None}})
    write_allowance(root, changed)
    print()
    print(f"  {bold('Set.')} It applies to the next job, not to runs already going.")
    for name, value in changed.as_dict().items():
        if name != "window_seconds":
            print(f"    {name:<28}: {value if value else 'no limit'}")
    return 0


def cmd_watch(args) -> int:
    """What this gateway looks like right now, and anything worth waking somebody for.

    An OPERATOR command rather than an address. Metrics name accounts, and an address that names
    accounts is one a customer could eventually reach; the command line is reached by whoever can
    log in to the machine, which is the operator by definition.
    """
    from agentnode_sdk.gateway import observability as obs

    root = _root(args)
    _state, service = _service(root)
    sink = obs.LocalFileSink(root / obs.EVENTS_NAME)
    seen = obs.observe(service, sink)
    counts = seen["counts"]

    print()
    print(f"  {bold('Right now')}")
    states = counts["runs_by_state"] or {"(nothing)": 0}
    print("    runs            : " + ", ".join("%s %d" % (k, v) for k, v in sorted(
        states.items())))
    if counts["capacity"]:
        print("    capacity        : " + ", ".join(
            "%s %d/%d" % (name, used, ceiling)
            for name, (used, ceiling) in sorted(counts["capacity"].items())))
    print(f"    customers       : {counts['accounts']} ({counts['devices']} device(s))")
    print(f"    cleanups unconfirmed: {counts['cleanups_not_confirmed']}")
    if counts["stopped_because"]:
        print(f"    {bold('not taking work')}: {counts['stopped_because']}")

    if counts["refusals_by_reason"]:
        print()
        print(f"  {bold('Refused in the last 15 minutes')}")
        for reason, how_many in sorted(counts["refusals_by_reason"].items(),
                                       key=lambda kv: -kv[1]):
            print(f"    {reason:<24} {how_many}")

    print()
    if seen["alerts"]:
        print(f"  {bold('Worth looking at')}")
        for alert in seen["alerts"]:
            print(f"    [{alert['severity']}] {alert['rule']}")
            print(f"      {alert['because']}")
            print(f"      {dim(alert['what_it_means'])}")
    else:
        print("  Nothing is asking for attention.")
    print()
    print(dim("  Written to %s as well, one JSON object per line, so a collector can read it"
              % (root / obs.EVENTS_NAME)))
    print(dim("  without this command. No provider is configured and none is needed."))
    return 0


def cmd_used(args) -> int:
    """What each client has used. What an operator asks before changing a limit."""
    from agentnode_sdk.gateway import meter

    root = _root(args)
    if getattr(args, "verify", False):
        held = meter.verify(root)
        print()
        if held["ok"]:
            print(f"  {bold('This record has not been altered.')}")
            print(f"  {held['lines']} line(s); {held['detail']}.")
            if held.get("unchecked"):
                print()
                print(f"  {bold('Not all of it.')} The first {held['unchecked']} line(s) predate")
                print("  the chain and are not evidence of anything. They were left unsigned on")
                print("  purpose: signing them now would be this gateway vouching for what it")
                print("  did not record at the time.")
            print()
            print(dim("  Tamper-evident, which is a smaller claim than tamper-proof: nobody"))
            print(dim("  without this gateway's meter key can change the file without the change"))
            print(dim("  showing up here. It says nothing about whether the gateway is honest --"))
            print(dim("  the process that writes a log cannot be checked by that log."))
            return 0
        print(f"  {bold('This record has been altered.')}")
        print(f"  {held['detail']}.")
        print()
        print(f"  Everything before line {held.get('at', 0)} still checks out. From there on it")
        print("  is not evidence of anything.")
        return 1

    totals = meter.summarise(root)
    if not totals:
        print()
        print("  Nothing has run here yet.")
        return 0
    print()
    print(f"  {bold('What each client has used')}")
    print(f"    {'client':<16} {'runs':>6} {'seconds':>10} {'bytes out':>12}")
    for who, what in sorted(totals.items()):
        print(f"    {who[:16]:<16} {what['runs']:>6} {what['seconds']:>10.1f} "
              f"{what['bytes_out']:>12}")
    print()
    print(dim("  This is a record of use. Nothing here is priced and nothing is charged."))
    print(dim("  To check that nothing in it has been altered:  agentnode gateway used --verify"))
    return 0


def dispatch(args) -> int:
    action = getattr(args, "gateway_command", None)
    handlers = {
        "init": cmd_init,
        "stop": cmd_stop,
        "resume": cmd_resume,
        "limits": cmd_limits,
        "used": cmd_used,
        "start": cmd_start,
        "status": cmd_status,
        "egress": cmd_egress,
        "doctor": cmd_doctor,
        "pair": cmd_pair,
        "clients": cmd_clients,
        "accounts": cmd_accounts,
        "export": cmd_export,
        "keeps": cmd_keeps,
        "sweep": cmd_sweep,
        "delete": cmd_delete,
        "watch": cmd_watch,
        "revoke": cmd_revoke,
        "verify": cmd_verify,
        "challenge": cmd_challenge,
    }
    handler = handlers.get(action)
    if handler is None:
        print("  Usage: agentnode gateway "
              "{init|start|status|egress|doctor|pair|clients|accounts|revoke|verify}")
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
