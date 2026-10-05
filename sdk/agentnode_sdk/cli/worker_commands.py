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

import pathlib

import base64
import os
import sys
from pathlib import Path

from agentnode_sdk.cli.output import bold
from agentnode_sdk.worker import (SEPARATE_WORKER_HOST, SINGLE_HOST_DEVELOPMENT, TOPOLOGIES)


def cmd_key(args) -> int:
    """Write the key the gateway and the worker authenticate messages with.

    One file, readable by both accounts and by nobody else. It is not a secret that protects the
    machine -- anything that is root here can read it, along with everything else -- it is what
    makes the protocol the same when the worker moves to a machine where that is not true.
    """
    from agentnode_sdk.worker import protocol as wire

    pair = str(getattr(args, "pair", "") or "").strip()
    if pair:
        return _pair_key(args, pair)

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


def _pair_key(args, pair: str) -> int:
    """Add or rotate the key for ONE gateway-and-worker pair.

    Separate from the shared key above rather than replacing it, because the shared key is
    still the right thing on one machine and the wrong thing across two. A rotation keeps the
    previous key alongside the new one so that work already in flight stays readable; ending
    that overlap is `--retire-overlap`, a second command, so it is something an operator does
    on purpose.
    """
    from agentnode_sdk.worker import pairkeys as _pairkeys
    from agentnode_sdk.worker import protocol as wire

    if pair.count(":") != 1 or not all(pair.split(":")):
        print()
        print("  A pair is <gateway-instance>:<worker-instance>, for example  g1:w1")
        return 2
    gateway, worker = pair.split(":")
    named = str(getattr(args, "at", "") or "").strip()
    if not named:
        print()
        print("  Where should it go? Pass --at <path>.")
        return 2
    at = Path(named)
    at.parent.mkdir(parents=True, exist_ok=True)

    try:
        ring = _pairkeys.Keyring.read(at) if at.exists() else _pairkeys.empty()
    except _pairkeys.KeyringRefused as refused:
        print()
        print("  " + refused.because)
        print("  " + refused.what_to_do)
        return 1

    held = (gateway, worker) in ring.pairs()
    if getattr(args, "retire_overlap", False):
        if not held:
            print()
            print(f"  There is no key for {gateway}<->{worker} at {at}.")
            return 1
        ring = ring.retire_overlap(gateway=gateway, worker=worker)
        ring.write(at)
        print()
        print(f"  The overlap for {gateway}<->{worker} has ended. Only the current key is")
        print("  accepted from now on; anything still sealed with the previous one is refused.")
        return 0

    fresh = base64.urlsafe_b64decode(wire.new_key())
    if held and not getattr(args, "force", False):
        ring = ring.rotate(gateway=gateway, worker=worker, key=fresh)
        now = ring.for_pair(gateway=gateway, worker=worker)
        ring.write(at)
        print()
        print(f"  Rotated the key for {gateway}<->{worker} at {at} (generation {now.generation}).")
        print("  The previous key is still accepted, so work already in flight is not lost.")
        print("  When both sides hold the new one, end the overlap:")
        print(f"    agentnode worker key --pair {pair} --at {at} --retire-overlap")
        return 0

    ring = ring.add(gateway=gateway, worker=worker, key=fresh)
    ring.write(at)
    print()
    print(f"  A key for {gateway}<->{worker} only is at {at}.")
    print("  It authenticates that one pair. It is not shared with any other worker, and a")
    print("  worker holding it cannot use it to speak as, or to, a different one.")
    print("  Copy this file to BOTH sides of that pair and to nowhere else.")
    return 0


def _this_account() -> str:
    """The account this process is running as, for a message that tells an operator what to fix."""
    try:
        import getpass

        return getpass.getuser()
    except Exception:                                         # noqa: BLE001 - never worth failing
        import os

        return str(getattr(os, "getuid", lambda: "?")())


def _refuse_unless_pinned(root, what: str) -> int:
    """A thin call into the one implementation, in `runtime_pin`.

    This file and the gateway's one each held their own copy of the whole rule. They had already
    started to disagree -- one was changed to refuse an unpinned start and the other was
    not -- which would have left a machine whose gateway refuses and whose worker shrugs.
    `bold` is handed in so each surface keeps its own emphasis without the rule moving.
    """
    from agentnode_sdk.gateway import runtime_pin

    return runtime_pin.refuse_unless_pinned(root, what, bold=bold)

def _tls_from(args):
    """The TLS settings these arguments describe, or None, or a refusal.

    Shared by `preflight` and `serve` so the two cannot judge the same arguments differently. A
    unit that validated its configuration with a second implementation would eventually permit
    something the product refuses, or refuse something it permits, and the operator would have
    no way to tell which.
    """
    from agentnode_sdk.worker.tls import TlsSettings

    listen = str(getattr(args, "listen", "") or "")
    parts = [getattr(args, name, None) for name in
             ("tls_dir", "trust", "deployment", "revocation_list", "floor")]
    if not (listen or any(parts) or getattr(args, "accept_gateway", None)):
        return None
    if not (listen and all(parts) and getattr(args, "accept_gateway", None)):
        raise ValueError(
            "a TLS door needs --listen, --tls-dir, --trust, --deployment, --revocation-list, "
            "--floor and at least one --accept-gateway. Part of that is refused rather than "
            "started without its checks.")
    folder = pathlib.Path(args.tls_dir)
    remote = str(getattr(args, "topology", "") or "") == SEPARATE_WORKER_HOST
    tombstones = str(getattr(args, "tombstones", "") or "")
    if remote and not tombstones:
        raise ValueError(
            "a worker on its own machine has no issuer inventory to consult, so it needs the "
            "signed list of withdrawn identities: --tombstones <file>. Without it, revoking a "
            "gateway and issuing it a new certificate under the same name brings it back.")
    return TlsSettings(certificate=str(folder / "cert.pem"), key=str(folder / "key.pem"),
                       anchor=str(args.trust), deployment=str(args.deployment),
                       accept=frozenset(args.accept_gateway),
                       revocation_list=str(args.revocation_list), floor=str(args.floor),
                       identity_tombstones=tombstones,
                       # Required exactly where it can be met: across the boundary. On one host
                       # the issuer's inventory is the authority and is right here.
                       tombstones_required=remote,
                       reload_seconds=float(args.trust_reload_seconds),
                       reevaluate_seconds=float(args.reevaluate_seconds))


#: Where install.sh puts the table of what a role needs on the host. A path and not an import:
#: the table is read by the install scripts before there is anything of ours installed to import.
WHERE_THE_TABLE_IS = "/opt/agentnode/deploy/prerequisites.py"


def cmd_preflight(args) -> int:
    """Everything `serve` checks about its configuration, without opening anything.

    A worker that refuses at start is correct and expensive: it has already proved a memory
    ceiling, and the reason is at the end of a page of output in a journal somebody has to know
    to read. This answers the same questions before any of that, in order, and says which one
    failed -- and it is what the unit runs as ExecStartPre, so the two cannot drift.

    It opens no socket, binds no address, starts no container and prints no key material. A key
    appears as the pair it belongs to and a generation; a certificate appears as the identity
    URI on it, which is a name.
    """
    say, bad = [], []

    def good(line):
        say.append("  ok      " + line)

    def refuse(line, what_to_do=""):
        bad.append((line, what_to_do))
        say.append("  REFUSED " + line)

    topology = str(getattr(args, "topology", "") or SINGLE_HOST_DEVELOPMENT)
    listen = str(getattr(args, "listen", "") or "")
    socket_at = str(getattr(args, "socket", "") or "")
    remote = topology == SEPARATE_WORKER_HOST

    print()
    print(f"  {bold('Would this worker start?')}  topology: {topology}")
    print()

    # 0. WHAT THE HOST ITSELF NEEDS, before any question about configuration. A configuration
    #    can be perfect on a host that cannot unpack an artefact or start a container, and the
    #    two machines of the cross-host run were exactly that: `tar` absent on both, no runtime
    #    on the worker, installed by hand because nothing checked.
    #
    #    The table is the install path's own table, installed beside it, so this check and the
    #    install cannot disagree about what the role needs.
    _prereq = _the_prerequisite_table()
    if _prereq is None:
        # ABSENT IS NOT THE SAME STATEMENT AS UNREADABLE. Absent means this host was not set up
        # by the deploy path -- a checkout, a developer's machine -- and a missing checker is not
        # a missing prerequisite, so saying "refused" here would be a lie about the host. It is
        # said out loud rather than passed over, because an operator reading a clean preflight is
        # entitled to know which checks ran.
        say.append("  note    no prerequisite table at %s, so what this host has was not checked. "
                   "A host installed by the deploy path has one." % WHERE_THE_TABLE_IS)
    elif isinstance(_prereq, str):
        # Present and unusable. That is this host's problem and not a reason to continue: a table
        # that cannot be read is the one case where carrying on would hide a real answer.
        refuse("the prerequisite table at %s cannot be read: %s" % (WHERE_THE_TABLE_IS, _prereq),
               "It is installed by install.sh from the artefact. Re-run the installer, or "
               "restore the file from the artefact this host was installed from.")
    else:
        for entry in _prereq["required_missing"]:
            refuse("%s is not on this host, and %s" % (entry["program"], entry["why"]),
                   "On this host, as root: %s" % " ".join(_prereq["to_install"]))
        if not _prereq["required_missing"]:
            good("every program this role needs is here (table version %d, %d checked)"
                 % (_prereq["table_version"], len(_prereq["present"]) + len(_prereq["missing"])))

    # 1. THE DOOR, and whether the address agrees with the arrangement. Checked first because
    #    every other answer is about a worker that is in one arrangement or the other.
    from agentnode_sdk.worker import topology as _topology

    if not (listen or socket_at):
        refuse("no door: a worker needs --socket, --listen, or both",
               "Give it one. Started with neither it would hold a container runtime and answer "
               "nobody.")
    if listen:
        try:
            _topology.check(topology, listen, where="--listen")
            good(f"the address agrees with the topology: {listen}")
        except _topology.TopologyRefused as refused:
            refuse(f"{refused.cause}: {refused.because}", refused.what_to_do)
    if socket_at and remote:
        refuse("a unix socket on a worker that is on its own machine can only be opened by "
               "something already on that machine, and nothing there is entitled to give work",
               "Leave --socket out.")

    # 2. THE PER-PAIR KEY. A key shared with everything means reaching one worker is reaching
    #    all of them, so across the boundary there is one key per pair and it is selected by the
    #    identity TLS proved -- never by anything the caller sent.
    keyring_at = str(getattr(args, "keyring", "") or "")
    if remote and not keyring_at:
        refuse("no --keyring, and a worker on its own machine does not authenticate its control "
               "plane with a key shared by everything",
               "agentnode worker key --pair <gateway>:<worker> --at <file>")
    elif keyring_at:
        from agentnode_sdk.worker import pairkeys as _pairkeys

        try:
            keyring = _pairkeys.Keyring.read(keyring_at)
            good("keyring readable: %d pair(s)" % len(keyring.pairs()))
        except _pairkeys.KeyringRefused as refused:
            refuse(f"{refused.cause}: {refused.because}", refused.what_to_do)

    # 3. THE JOURNAL. Over a network a retry is ordinary and every retry carries a fresh nonce,
    #    so nothing else would stop the same job running twice.
    journal_at = str(getattr(args, "journal", "") or "")
    if remote and not journal_at:
        refuse("no --journal, and a worker reached over a network must be able to say whether it "
               "has already run a job",
               "Start it with --journal <directory> on this machine's own disk.")
    elif journal_at:
        from agentnode_sdk.worker import journal as _journal

        try:
            book = _journal.Journal(journal_at)
            good("journal writable at %s: %d record(s), %d unsettled"
                 % (journal_at, book.count(), len(book.unsettled())))
        except _journal.JournalRefused as refused:
            refuse(f"{refused.cause}: {refused.because}", refused.what_to_do)
        except OSError as exc:
            refuse("the journal directory cannot be used: %s" % exc,
                   "It must be writable by the account this worker runs as.")

    # 4. THE TLS DOOR, judged the way the gateway will judge it. Not "do the files exist": the
    #    question worth answering is whether a control plane dialling this worker right now
    #    would accept the certificate it is about to present -- valid at the effective time, the
    #    right usage, the right name, not revoked and not withdrawn.
    try:
        tls = _tls_from(args)
    except ValueError as exc:
        refuse(str(exc))
        tls = None
    if tls is not None:
        from agentnode_sdk.pki import identity as _identity
        from agentnode_sdk.pki.trust import TrustView
        from agentnode_sdk.worker.tls import own_instance

        # `identity` and not just `role`: since a floor names whose it is, a view built without
        # one refuses every floor, including this worker's own. Preflight built one that way and
        # therefore reported a healthy worker as unable to serve -- found by bringing a real pair
        # up, not by reading. `settings.trust()` is the one place that knows how to answer this,
        # so preflight asks it rather than assembling a second, subtly different view.
        view = tls.trust("worker")
        try:
            instance = own_instance(tls)
            good("this worker's certificate names it %s" % instance)
            from cryptography import x509

            with open(tls.certificate, "rb") as handle:
                mine = x509.load_pem_x509_certificate(handle.read())
            _identity.check_peer(mine.public_bytes(_serialization().Encoding.DER),
                                 deployment=tls.deployment, expected_role="worker",
                                 accept_instances={instance}, trust=view)
            good("a control plane dialling now would accept it")
        except _identity.PeerRefused as refused:
            refuse("this worker's own certificate would be refused: %s" % refused,
                   "Until this is fixed the worker will start and every connection will fail.")
        except FileNotFoundError as missing:
            refuse("a TLS file is missing: %s" % missing.filename,
                   "Enrol this worker before starting it.")
        except OSError as exc:
            refuse("a TLS file cannot be read: %s" % exc)

        # WHAT ENROLMENT LEFT. Reported, not removed: a check that changes what it is checking
        # is not a check, and this one runs as ExecStartPre where a surprise deletion would be
        # the last thing anybody expects. `serve` removes them, and says so.
        from agentnode_sdk.pki.enrolment import ENROLMENT_RESIDUES

        here = pathlib.Path(str(getattr(args, "tls_dir", "") or ""))
        left = [n for n in ENROLMENT_RESIDUES if (here / n).is_file()]
        if left:
            say.append("  note    enrolment left %s here; the worker removes %s when it starts"
                       % (", ".join(left), "them" if len(left) > 1 else "it"))
        else:
            good("nothing is left of the enrolment")

        # The floor, named separately, because it is the one thing that is written by root on
        # THIS machine and is the step most often forgotten -- a floor copied from the control
        # plane is keyed to that kernel's boot and is unusable here.
        try:
            view.effective_time()
            good("the time floor at %s is usable" % tls.floor)
        except Exception as exc:                              # noqa: BLE001 - reported, not raised
            # NOT `pki tick`. That is the issuer's run: it reads the inventory and publishes the
            # revocation list, neither of which a worker host has, and telling an operator to
            # run it here is how a worker ended up with no floor at all.
            refuse("the time floor is not usable: %s" % exc,
                   "As root ON THIS HOST: `agentnode pki floor advance --role worker "
                   "--certificate %s --anchor %s`, and check that "
                   "agentnode-floor-advance.timer is enabled here."
                   % (tls.certificate, tls.anchor))

    print("\n".join(say))
    print()
    if bad:
        print(f"  {bold('This configuration would not serve.')}")
        for line, what_to_do in bad:
            if what_to_do:
                print("    - " + what_to_do)
        print()
        return 1
    print("  Nothing was opened, bound or started. Every answer above is about configuration.")
    print()
    return 0


def _the_prerequisite_table():
    """The installed table's answer for the worker role, or None if no table is installed here.

    Returns the report dict on success, the reason as a STRING when the table is there and cannot
    be used, and None when there is no table at all -- three answers, because the caller must
    treat them differently and a single falsy value would collapse two of them.

    It is loaded from a path rather than imported, because it is deliberately not part of the
    package: the install scripts read it before a virtual environment exists, so it lives in the
    artefact and is installed beside it. One file, two readers.
    """
    import importlib.util

    where = pathlib.Path(WHERE_THE_TABLE_IS)
    if not where.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("agentnode_host_prerequisites", str(where))
        if spec is None or spec.loader is None:
            return "it is not loadable as a python module"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.inspect("worker")
    except Exception as exc:                        # noqa: BLE001 - reported as a refusal, above
        return "%s: %s" % (type(exc).__name__, exc)


def _serialization():
    from cryptography.hazmat.primitives import serialization

    return serialization


def cmd_serve(args) -> int:
    """Serve one socket, for one account, until something stops this process."""
    from agentnode_sdk.worker.reconcile import LeftoversRemain
    from agentnode_sdk.worker.service import CannotHoldItsLimits, serve

    # The worker is where foreign code actually runs, so it is the LAST place that should be
    # allowed to run on an interpreter nobody tested. Its pin lives beside its key rather than
    # in the gateway's state, because the two are separate accounts and a worker must not need
    # to read the gateway's directory to know what it is.
    _pin_root = pathlib.Path(str(getattr(args, "key", "") or "/etc/agentnode")).parent
    if _refuse_unless_pinned(_pin_root, "worker"):
        return 1
    address = str(getattr(args, "socket", "") or "")
    key = str(getattr(args, "key", "") or "")
    for_whom = getattr(args, "for_user", None)
    # A door is required, and the socket is no longer the only one that counts as one: a
    # deployment that has moved to mutual TLS should not have to keep a second way in.
    a_tls_door = bool(getattr(args, "listen", "") or "")
    remote = str(getattr(args, "topology", "") or "") == SEPARATE_WORKER_HOST
    keyring_at = str(getattr(args, "keyring", "") or "")
    # --key NAMES THE KEY SHARED WITH ONE GATEWAY ON THIS MACHINE, and a worker on its own
    # machine does not have one: it holds a key per pair, in the keyring, chosen by the identity
    # the handshake proved. Requiring it there would be asking for a file whose only use would
    # be as a fallback nobody wants.
    needs_a_shared_key = not (remote and keyring_at)
    # --for-user names the ONE LOCAL ACCOUNT allowed to open the unix socket, checked with
    # SO_PEERCRED. With no socket there is no local peer to name: the caller is on another
    # machine and what decides who may speak is the certificate. Required where it does
    # something, which is where there is a socket.
    needs_an_account = bool(address)
    if (not (address or a_tls_door) or (needs_a_shared_key and not key)
            or (needs_an_account and not for_whom)):
        print()
        print("  A worker needs a door, something to authenticate messages with, and the one")
        print("  account that may speak to it. The door is a unix socket, mutual TLS, or both:")
        print("    agentnode worker serve --socket unix:///run/agentnode/worker.sock \\")
        print("                           --key /etc/agentnode/worker.key \\")
        print("                           --for-user agentnode-gateway")
        print("  or, with no socket at all:")
        print("    agentnode worker serve --listen tcps://127.0.0.1:8443 --tls-dir <dir> \\")
        print("                           --trust <ca.pem> --deployment <id> \\")
        print("                           --accept-gateway <instance> --revocation-list <file> \\")
        print("                           --floor <file> --key /etc/agentnode/worker.key \\")
        print("                           --for-user agentnode-gateway")
        print("  or, on its own machine, where the keyring replaces the shared key and there is")
        print("  no local account to name:")
        print("    agentnode worker serve --topology %s \\" % SEPARATE_WORKER_HOST)
        print("                           --listen tcps://<private-ip>:8443 --tls-dir <dir> \\")
        print("                           --keyring <file> --journal <dir> ...")
        return 2
    uid = None
    if for_whom is not None and str(for_whom) != "":
        try:
            uid = int(for_whom)
        except ValueError:
            import pwd

            try:
                uid = pwd.getpwnam(str(for_whom)).pw_uid
            except KeyError:
                print()
                print(f"  There is no account called {for_whom} on this machine, so there is "
                      "nobody")
                print("  to serve. A worker is started for one account and refuses to guess.")
                return 1

    print()
    print(f"  {bold('AgentNode sandbox worker')}")
    print("  This account is the only one that drives a container runtime. It holds no pairing")
    print("  state, no signing identity and no client's token.")
    # Neither half of this assumes a socket any more. The sentence about the ceiling had been
    # written BETWEEN the two halves of the sentence above, so both read as nonsense; and it
    # promised a socket to every worker, including one whose only door is mutual TLS.
    print("  Before any door opens it hits a memory ceiling, to see whether one binds.")
    listen = str(getattr(args, "listen", "") or "")
    # The same reading of the same arguments `preflight` does, in one place. It was written out
    # twice, which is how an ExecStartPre ends up validating a different configuration from the
    # one ExecStart uses -- and then the check passes and the service does not start.
    try:
        tls = _tls_from(args)
    except ValueError as refusal:
        print()
        print("  " + str(refusal))
        return 2
    try:
        serve(address, key, uid, tls_address=listen, tls=tls,
              topology=str(getattr(args, "topology", "") or SINGLE_HOST_DEVELOPMENT),
              keyring_path=str(getattr(args, "keyring", "") or ""),
              journal_at=str(getattr(args, "journal", "") or ""))
    except KeyboardInterrupt:                                 # pragma: no cover - operator
        print("\n  stopped.")
        return 0
    except LeftoversRemain as refusal:
        # CU5: this is a CLEANUP state and it is never shaped like a policy decision. In F37 the
        # symptom reached an operator as `403 Forbidden` from the egress proxy -- indistinguishable
        # from an allowlist refusal -- while the real condition was that the account could not remove
        # its own containers and nothing on any custom network could resolve a name.
        print()
        # THE HEADLINE HAS TO BE TRUE OF THIS REFUSAL, not of the family it belongs to. There are two
        # states here and they are not the same thing to act on: a resource this account still owns and
        # could not remove, and a runtime that could not be ASKED what is there. Printing the first when
        # the second happened sends an operator looking for a container that does not exist -- which is
        # what it did when a listing template podman accepts and docker rejects made the whole inventory
        # unaskable, and a lane with nothing of ours on it read as a lane that owned something.
        owned = [row for row in refusal.resources if row.get("name")]
        if owned:
            print(f"  {bold('This worker will not serve: it owns something it could not remove.')}")
        else:
            print(f"  {bold('This worker will not serve: it cannot say what is on this host.')}")
        print()
        print("  " + str(refusal.reason))
        print()
        for row in refusal.resources:
            print("    %-16s %s" % (row.get("kind") or "resource", row.get("name") or "?"))
            if row.get("state"):
                print("      the runtime still says: %s" % row["state"])
            if row.get("why"):
                print("      and said: %s" % row["why"])
        print()
        if not owned:
            print("  Nothing is named above because nothing could be listed. An answer nobody can give")
            print("  is not an empty one, so this refuses rather than assuming the host is clean.")
            print()
        print("  Nothing was opened and no job can reach this machine, and the runtime's namespace")
        print("  was NOT rebuilt. That order is deliberate: rebuilding it is what takes away this")
        print("  account's ability to remove its own containers, and a worker that serves with a")
        print("  route out standing is the thing this refusal exists to prevent.")
        print()
        print("  This is not a policy refusal and not a 403. The destinations a job may reach are")
        print("  not involved.")
        return 1
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


def cmd_reconcile(args) -> int:
    """Remove everything of this worker's that no run is waiting for, and say whether that worked.

    THIS IS WHAT THE RUNTIME UNIT CALLS, in place of a bare `podman system migrate || true`. The unit
    runs as the worker's own account and without the worker's hardening, which is the only place a
    rootless namespace can be rebuilt -- and `--before-migrate` is the gate that decides whether it may
    be: it reconciles, reads every removal back, and exits non-zero if anything of ours is still there
    or any listing could not be read. systemd stops a `oneshot` at the first ExecStart that fails, so
    the migration that follows in the unit simply does not run.
    """
    from agentnode_sdk.worker.reconcile import reconcile, reconcile_then_rebuild

    before_migrate = bool(getattr(args, "before_migrate", False))
    said = reconcile_then_rebuild() if bool(getattr(args, "rebuild", False)) else reconcile()
    report = said.report.get("before", said.report)
    print("  runtime        : %s" % (report.get("runtime") or "none"))
    print("  found          : %s" % ", ".join(report.get("found") or []) or "-")
    print("  removed        : %s" % ", ".join(report.get("removed") or []) or "-")
    egress = report.get("egress") or {}
    print("  of its route out: removed %d, failed %d"
          % (len(egress.get("removed") or []), len(egress.get("failed") or [])))
    for row in (egress.get("removed") or []):
        print("      gone  %-14s %s" % (row.get("kind") or "?", row.get("name") or "?"))
    if said.clean:
        print("  nothing of ours is left, and every listing could be read")
        if before_migrate:
            print("  so the namespace may be rebuilt by the step after this one")
        return 0
    print("  NOT clean: %s" % said.why)
    if before_migrate:
        print("  so the namespace must NOT be rebuilt: doing it now would take away this account's")
        print("  ability to remove what is still there. Nothing after this step runs.")
    return 1


def dispatch(args) -> int:
    action = getattr(args, "worker_command", None)
    handlers = {"key": cmd_key, "serve": cmd_serve, "preflight": cmd_preflight,
                "reconcile": cmd_reconcile}
    if action not in handlers:
        print()
        print("  agentnode worker key       --at <path>")
        print("  agentnode worker serve     --socket <unix://...> --key <path> "
              "--for-user <account>")
        print("  agentnode worker preflight <the same arguments as serve>")
        return 2
    return handlers[action](args)


def add_parser(subparsers) -> None:
    """The `worker` verb, in the one place the CLI's shape is decided."""
    worker = subparsers.add_parser(
        "worker", help="Run the account that executes sandboxed code")
    actions = worker.add_subparsers(dest="worker_command")

    key = actions.add_parser("key", help="Make the key the gateway and this worker share")
    key.add_argument("--at", default="", metavar="PATH")
    key.add_argument("--pair", default="", metavar="GATEWAY:WORKER",
                     help="make a key for ONE pair instead of a key shared with everything; "
                          "required once the worker is on another machine")
    key.add_argument("--retire-overlap", dest="retire_overlap", action="store_true",
                     help="with --pair: stop accepting the previous key after a rotation")
    key.add_argument("--force", action="store_true",
                     help="Replace a key that is already there, and stop every gateway holding "
                          "the old one from reaching this worker")

    # ONE SET OF ARGUMENTS, TWO COMMANDS. `preflight` answers "would this configuration serve"
    # and `serve` serves it, and they have to be told the same things in the same words -- a
    # check that takes a slightly different set is a check of a slightly different deployment.
    def the_same_arguments(p):
        p.add_argument("--socket", default="", metavar="ADDRESS",
                       help="unix:///run/agentnode/worker.sock")
        p.add_argument("--key", default="", metavar="PATH",
                       help="the key shared with one gateway on this machine; not used, and not "
                            "required, with --topology %s and a --keyring"
                            % SEPARATE_WORKER_HOST)
        p.add_argument("--for-user", dest="for_user", default=None, metavar="ACCOUNT",
                       help="the account the gateway runs as; nothing else may open the socket. "
                            "Required only where there IS a socket: with no socket the caller is "
                            "on another machine and there is no local account to name")
        # The mutual-TLS door. It may stand beside the socket or replace it; what it may not do
        # is stand half-configured. All of these together or none of them.
        p.add_argument("--listen", default="", metavar="ADDRESS",
                       help="tcps://<literal-ip>:<port> -- loopback unless --topology says "
                            "otherwise")
        # WHICH ARRANGEMENT THIS WORKER IS IN, in its own words rather than the gateway's. The
        # worker judges its own address against this and refuses a disagreement on its own, so a
        # worker placed on its own machine will not quietly bind a loopback address because
        # whoever dials it believes it is local.
        p.add_argument("--journal", default="", metavar="DIR",
                       help="where this worker writes down what it has been asked to run; "
                            "required with --topology %s" % SEPARATE_WORKER_HOST)
        p.add_argument("--keyring", default="", metavar="FILE",
                       help="per-pair keys; required with --topology %s"
                            % SEPARATE_WORKER_HOST)
        p.add_argument("--topology", default=SINGLE_HOST_DEVELOPMENT, metavar="NAME",
                       choices=list(TOPOLOGIES),
                       help="%s (default) or %s -- must agree with --listen"
                            % (SINGLE_HOST_DEVELOPMENT, SEPARATE_WORKER_HOST))
        p.add_argument("--tls-dir", dest="tls_dir", default="", metavar="DIR",
                       help="where this worker's cert.pem and key.pem are")
        p.add_argument("--trust", default="", metavar="FILE",
                       help="the deployment's CA certificate, and nothing else")
        p.add_argument("--deployment", default="", metavar="ID")
        p.add_argument("--accept-gateway", dest="accept_gateway", action="append", default=[],
                       metavar="INSTANCE", help="a gateway instance this worker accepts")
        # Stage 5: what the TLS door judges a caller by, besides its certificate. Required with it.
        p.add_argument("--revocation-list", dest="revocation_list", default="", metavar="FILE",
                       help="the deployment's signed revocation list")
        # Stage 9, reachable from stage 12. Revocation is by serial, so a gateway that is given a
        # new certificate under the same name comes straight back; this is the signed list of
        # names that were taken away for good. Required across the boundary, where there is no
        # issuer inventory to consult instead.
        p.add_argument("--tombstones", default="", metavar="FILE",
                       help="the deployment's signed list of withdrawn identities; required "
                            "with --topology %s" % SEPARATE_WORKER_HOST)
        p.add_argument("--floor", default="", metavar="FILE",
                       help="this worker's time floor, written by root and read here")
        p.add_argument("--trust-reload-seconds", dest="trust_reload_seconds", type=float,
                       default=10.0, metavar="S",
                       help="reread the list and the floor at least this often")
        p.add_argument("--reevaluate-seconds", dest="reevaluate_seconds", type=float,
                       default=5.0, metavar="S",
                       help="judge every open TLS connection again this often")

    the_same_arguments(actions.add_parser(
        "serve", help="Listen for the one account, or the one gateway, that may speak here"))
    the_same_arguments(actions.add_parser(
        "preflight", help="Would this configuration serve? Opens nothing, starts nothing"))

    # WHAT THE RUNTIME UNIT CALLS. It takes none of the arguments above: it needs no door, no keyring
    # and no trust material, because removing what this account owns is not about who may speak here.
    tidy = actions.add_parser(
        "reconcile", help="Remove what no run is waiting for, and say whether that worked")
    tidy.add_argument("--before-migrate", dest="before_migrate", action="store_true",
                      help="say, in the refusal, that the namespace must not be rebuilt after this")
    tidy.add_argument("--rebuild", dest="rebuild", action="store_true",
                      help="and rebuild the rootless namespace afterwards, but only if nothing of "
                           "ours is left")


__all__ = ["add_parser", "dispatch", "cmd_key", "cmd_preflight", "cmd_reconcile", "cmd_serve", "sys"]
