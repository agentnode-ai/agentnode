"""A worker reached over a socket. Today that socket is always on this machine.

What decides where the worker is, is an address in the gateway's configuration and nothing else:
no product code names a path, an account or a host. That is what `ALPHA-BOUNDARY-0001` needs to
be true before the second machine exists, and it is true.

**It does not follow that the worker can be moved by changing that string, and an earlier version
of this docstring said it did.** `from_address` below speaks `unix://` and `unix+stream://`, and
`tcps://` -- mutual TLS -- on a literal LOOPBACK address only (`worker/tls.py`). It refuses every
other scheme and every non-loopback host in as many words. So moving the worker to another
machine is still a change to the product, not to a deployment: the loopback restriction is in
this code, and lifting it is the transport decision's post-loopback gate, with its own review.

## Where the topology in the record comes from

Not from the worker. A worker asked to describe itself would be a worker whose answer about where
it is cannot be checked, and a record carrying that would be carrying a self-report. It comes from
the CONNECTION: a unix socket cannot cross a machine, so a worker reached over one is on this host
and that is not an opinion. A worker reached over a network address that is not loopback is not.

## Fail-closed, in every direction

A worker that is not running, one that accepts and never answers, one that answers after the
deadline, one whose answer does not verify, and one that answers about a different request are all
the same thing to a caller: nobody said what happened. None of them becomes a job that failed --
that would be telling a client something nobody established -- and none of them becomes a job that
worked.
"""
from __future__ import annotations

import socket
import struct
import time
from collections import OrderedDict
from urllib.parse import urlparse

from agentnode_sdk.worker import (
    Ceilings,
    NoRuntimeThere,
    Recovered,
    SEPARATE_WORKER_HOST,
    SINGLE_HOST_DEVELOPMENT,
    CouldNotRestrictTheNetwork,
    Gone,
    Isolation,
    Job,
    JobFailed,
    Outcome,
    Worker,
    WorkerUnreachable,
)
from agentnode_sdk.worker import protocol as wire
from agentnode_sdk.worker import topology as _topology
from agentnode_sdk.worker.service import _build_identity

#: How long to wait for the answer to a question that is not a job. Connecting, describing,
#: stopping and looking are all quick or they are not happening.
QUICK_SECONDS = 60.0

#: What to add to a job's own wall clock before giving up on the worker. The job's limit is the
#: worker's to enforce; this is only how long the control plane waits for it to say so, and it is
#: longer so that "the sandbox stopped it at its limit" arrives instead of being cut off.
RUN_MARGIN_SECONDS = 120.0

#: How long a measurement may take before the gateway stops waiting for it. It runs several
#: short containers on the worker and the doctor tells an operator it takes "a minute or two",
#: so ten minutes is generous; it used to be THIRTY, which mattered because it was also
#: exactly `ActivationLock.stale_after`.
#:
#: That coincidence was the real defect behind "another change to this gateway's policy is
#: already running". The lock was never leaked -- it is a file lock released in `__exit__` on
#: every path. What happened is that a transport loss which produces no reset left this call
#: blocked in `recv` for the full half hour, so the holder was genuinely alive and the lock's
#: staleness backstop could not fire before the call it guards had given up. Every later
#: measurement was refused, correctly, for thirty minutes.
#:
#: The bound has to be shorter than the backstop, and `test_a_wedged_measurement.py` asserts
#: it. Keepalive in `_open` is what makes the common case shorter still.
MEASURE_SECONDS = 600.0

#: A dead peer that never sent a reset is invisible to `recv`, which is why a bound alone is
#: not enough. These make the kernel ask: after 30s of silence, probe every 10s, give up after
#: 3 -- so a worker whose host vanished is noticed in about a minute rather than at the
#: timeout. Named rather than inlined because the three only mean something together.
KEEPALIVE_IDLE_SECONDS = 30
KEEPALIVE_INTERVAL_SECONDS = 10
KEEPALIVE_FAILURES = 3


def _ask_the_kernel_to_notice_a_dead_peer(connection) -> None:
    """Turn on TCP keepalive, so a peer that vanished without a reset is noticed.

    A machine that is powered off, partitioned, or whose packets are being dropped sends no
    reset, so `recv` waits for the full timeout however long that is. The gateway's measure
    call waited thirty minutes on exactly that, and because the activation lock's staleness
    backstop was also thirty minutes, the lock could not be judged stale before the call gave
    up -- so every later measurement was refused for half an hour.

    Best effort by design: not every platform has the three per-socket options, and a
    deployment whose kernel lacks them is not a deployment that should fail to connect. The
    bound in `MEASURE_SECONDS` is what makes it correct; this is what makes it quick.
    """
    try:
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except (OSError, AttributeError):                         # pragma: no cover - platform
        return
    for name, value in (("TCP_KEEPIDLE", KEEPALIVE_IDLE_SECONDS),
                        ("TCP_KEEPINTVL", KEEPALIVE_INTERVAL_SECONDS),
                        ("TCP_KEEPCNT", KEEPALIVE_FAILURES)):
        option = getattr(socket, name, None)
        if option is None:                                    # pragma: no cover - platform
            continue
        try:
            connection.setsockopt(socket.IPPROTO_TCP, option, value)
        except OSError:                                       # pragma: no cover - platform
            pass


def topology_of(address: str) -> str:
    """Where a worker reached at this address is, as far as the address can establish it.

    A unix socket does not leave the machine, so this is not a claim anybody made -- it is what
    the address IS. Loopback is the same: a connection to 127.0.0.1 is a connection to here.
    """
    parsed = urlparse(address or "")
    if parsed.scheme in ("unix", "unix+stream"):
        return SINGLE_HOST_DEVELOPMENT
    # `worker/topology.py` rather than `gateway/transport.py`: the worker package has to be
    # installable on a machine with no control plane on it, and this was the import that stopped
    # that being true.
    if _topology.is_loopback(parsed.hostname or ""):
        return SINGLE_HOST_DEVELOPMENT
    return SEPARATE_WORKER_HOST


def _path_of(address: str) -> str:
    parsed = urlparse(address or "")
    if parsed.scheme not in ("unix", "unix+stream"):
        raise WorkerUnreachable(
            "this build reaches a worker over a unix socket, and " + repr(address[:60]) + " is "
            "not one. A worker on another machine needs a transport this build does not have yet.")
    return parsed.path or ""


class SocketWorker(Worker):
    """The control plane's end of the line."""

    def __init__(self, address: str, key: bytes, connect_timeout: float = 10.0,
                 run_margin: float = RUN_MARGIN_SECONDS,
                 topology: str = SINGLE_HOST_DEVELOPMENT) -> None:
        #: WHAT SOMEBODY DECLARED, not what the address looks like. `topology_of` is still here
        #: and still right about what an address IS, but a record should say what arrangement
        #: was CHOSEN -- and `worker/topology.py` has already refused the pair if the two
        #: disagree, so by the time this runs there is nothing to choose between.
        self._topology = topology
        self.address = address
        self._key = key
        #: Set by a caller that has one. When it is set, the single key above is never used:
        #: every frame is sealed with the key belonging to the pair the handshake proved.
        self._keyring = None
        self._own_instance = ""
        self.connect_timeout = connect_timeout
        #: How long past a job's OWN wall clock this gateway keeps waiting before it decides the
        #: worker has said nothing. It cannot be small: a worker running a job legitimately says
        #: nothing until the job is done, so the wait has to cover the job first. It is a
        #: parameter because how much slack a deployment allows is a property of the deployment
        #: -- and because a test cannot otherwise establish what happens after it elapses
        #: without waiting out the default.
        self.run_margin = run_margin
        self._described: dict | None = None
        #: The wire version both sides were shown to speak. Empty until `describe` has run.
        self._agreed = ""
        #: The fencing token this gateway holds, and the machinery that keeps it alive. Only
        #: used where a worker issues them, which is where there is a takeover to fence against.
        import threading as _threading

        self._leasing = False
        self._lease_epoch: int | None = None
        self._renew_within = 5.0
        self._lease_lock = _threading.Lock()
        self._heartbeat = None
        self._stop_beating = None
        #: Who answered the describe, when the transport proves it. None on a socket.
        self._described_by = None
        #: run_id -> the identity the connection carrying that run proved. Bounded, oldest out.
        self._ran_on: "OrderedDict[str, object]" = OrderedDict()

    # ------------------------------------------------------------------ the line itself

    @property
    def topology(self) -> str:                                # type: ignore[override]
        return self._topology

    #: How this gateway reaches the worker, for the record to say. `worker/tls.py` overrides it.
    transport = "unix"

    def _open(self, budget: float | None = None):
        """A connected stream to the worker, and who it proved to be -- None on a socket, where
        the kernel's account check on the worker's side is what stands in for an identity.

        `budget` caps the whole of it for a caller that has a deadline of its own. None keeps
        the allowance a job has always had.
        """
        if not hasattr(socket, "AF_UNIX"):
            raise WorkerUnreachable(
                "this machine has no unix sockets, so it cannot reach a worker over one. The "
                "gateway and its worker run on Linux; a client does not need either.")
        try:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(self.connect_timeout if budget is None else budget)
            connection.connect(_path_of(self.address))
        except OSError as exc:
            raise WorkerUnreachable(
                "the sandbox worker at " + self.address + " could not be reached: " + str(exc)
            ) from exc
        return connection, None

    def confirm_reachable(self, budget: float | None = None) -> None:
        """Open the connection a run would use -- over TLS that is the handshake and the identity
        checks -- and close it without sending anything. Raises `WorkerUnreachable` as a run
        would, which the gateway turns into a refusal before it claims anything."""
        connection, _who = self._open(budget)
        try:
            connection.close()
        except OSError:                                       # pragma: no cover
            pass

    def _ask(self, method: str, params: dict, *, wait: float, run_id: str = "",
             _lease_was_retaken: bool = False) -> object:
        """One question and its answer, or a refusal that says which kind it is."""
        asked_with = params
        started_asking = time.time()
        # BEFORE ANY WORK CROSSES. `describe` negotiates; everything that carries or acts on
        # work waits for that to have happened. A mismatch therefore refuses before a job is
        # sent rather than after one has been half-processed.
        #
        # AND BOUNDED BY THE CALLER'S OWN BUDGET. The handshake used to wait `QUICK_SECONDS`
        # flat, so a worker that accepted the connection and then said nothing held the gateway
        # for a minute before a job allowed one second was given up on -- the wait stopped being
        # the job's, which is the property `test_socket_worker.py::
        # TestAWorkerThatTakesTheCallAndSaysNothing` exists to defend. It caught this on Linux
        # in CI; the whole file is skipped on Windows, where there are no unix sockets, so the
        # local suite could not have.
        if method in self.JOB_BEARING and not self._agreed:
            self._describe(wait=min(wait, QUICK_SECONDS))
        # AND A LEASE, TAKEN BECAUSE WORK IS ABOUT TO CROSS rather than because somebody
        # remembered to switch it on. `lease_from_the_worker()` existed, was documented as
        # idempotent, and had no caller anywhere -- not in the product and not in a test -- so
        # `_leasing` was never true, no `lease_epoch` was ever attached, and a worker on another
        # machine refused every job-bearing call with "this worker holds no lease, so nothing
        # may give it work". The worker was right; nothing on this side had ever asked.
        #
        # Found by bringing a real pair up: the FIRST thing the control plane did with its
        # worker was `gateway doctor --measure`, and it was refused. On one host nothing had
        # noticed, because the tests that prove the fencing drive the worker end directly.
        if method in self.JOB_BEARING and self._leasing:
            params = dict(params)
            params["lease_epoch"] = self._hold_a_lease(wait=min(wait, QUICK_SECONDS))
        deadline = time.time() + wait
        body = wire.request(method, params, deadline=deadline)
        connection, who = self._open()
        if who is not None:
            # The identity THIS connection proved, noted before a byte is sent -- so that a run
            # whose connection is lost afterwards is still recorded against who was checked.
            if run_id:
                self._note_who_ran(run_id, who)
            if method == "describe":
                self._described_by = who
        # WHICH KEY, decided by who answered rather than by anything on the wire. On a socket
        # there is no proved identity and no keyring, and the single key is what there is.
        seal_with, accept = self._keys_for(who)
        try:
            connection.settimeout(wait)
            connection.sendall(wire.seal(body, seal_with))
            with connection.makefile("rb") as stream:
                answered = wire.read_frame(stream, accept)
        except (OSError, wire.ProtocolError) as exc:
            # An answer that cannot be believed is not an answer. Every one of these is the
            # worker not having said anything, and none of them is a job that failed.
            raise WorkerUnreachable(
                "the sandbox worker at " + self.address + " did not give an answer this gateway "
                "could believe: " + str(exc)) from exc
        finally:
            try:
                connection.close()
            except OSError:                                   # pragma: no cover
                pass

        if answered.get("request_id") != body["request_id"]:
            raise WorkerUnreachable(
                "the sandbox worker answered about a different request, so nothing it said is "
                "about this one")
        try:
            return self._interpret(answered)
        except WorkerUnreachable as refused:
            # A LEASE THAT LAPSED IS NOT AN UNREACHABLE WORKER, and this is where the two used
            # to be confused. The worker was answering, promptly and correctly; it was refusing
            # because nothing held a lease. Nothing on this side could tell, because the cause
            # was flattened away, and nothing cleared the cached epoch -- so every later call
            # presented the same dead number and only restarting the process helped.
            #
            # One retry, and only one: if the worker refuses a freshly taken lease, that is a
            # real refusal and looping on it would turn a clear answer into a hang.
            if not self._worth_retaking_the_lease(method, refused, already=_lease_was_retaken):
                raise
            left = wait - (time.time() - started_asking)
            if left <= 0:
                raise
            self._forget_the_lease()
            return self._ask(method, asked_with, wait=left, run_id=run_id,
                             _lease_was_retaken=True)

    def result(self, run_id: str, *, wait: float = QUICK_SECONDS) -> Recovered:
        """Ask the worker what became of a run. Quick: it is a file read on the other side.

        `wait` is a parameter because the two callers want different things from it. Recovery
        after a restart can afford to be patient. The attempt made in the middle of a lost
        connection cannot: the run's terminal state is not published until it returns, so a
        long wait there turns one broken connection into a client waiting a minute for an
        answer that was never going to come.
        """
        said = self._ask("result", {"run_id": str(run_id)}, wait=wait)
        if not isinstance(said, dict):                        # pragma: no cover - refused first
            return Recovered(known=False)
        return Recovered(known=bool(said.get("known")),
                         # DEFAULT TRUE for a worker from before this field existed: it answered
                         # `known` at all, which a journal-less worker's own `_result` refuses to
                         # do -- it raises `journal-refused` instead of returning a shape.
                         keeps_a_record=bool(said.get("keeps_a_record", True)),
                         state=str(said.get("state") or ""),
                         outcome=said.get("outcome"),
                         cleanup=said.get("cleanup"),
                         unknown_outcome=bool(said.get("unknown_outcome")),
                         never_ran=bool(said.get("never_ran")),
                         ran_for=(float(said["ran_for"])
                                  if said.get("ran_for") is not None else None))

    def acknowledge(self, run_id: str) -> None:
        """Best effort. The worker's retention window does not depend on this arriving."""
        try:
            self._ask("result", {"run_id": str(run_id), "acknowledge": True},
                      wait=QUICK_SECONDS)
        except Exception:                                     # noqa: BLE001 - never worth failing
            pass

    def _keys_for(self, who):
        """(the key to seal with, the keys an answer may carry). One key unless a keyring says
        otherwise; a keyring is required only where a boundary is crossed, and `GatewayService`
        is what decides that."""
        if self._keyring is None:
            return self._key, self._key
        pair = self._keyring.for_pair(gateway=self._own_instance,
                                      worker=str(getattr(who, "instance", "") or ""))
        return pair.current, pair.accepted()

    def _interpret(self, answered: dict):
        """What an answer MEANS. Named, because the meaning is the thing worth testing.

        There are three ways a run does not produce a result, and they send a person to three
        different places: nobody answered (the network, the socket, the service), the worker
        answered and has no runtime (that host), and the job ran and failed (their own code).
        Collapsing the middle one into the first sent people to look at a connection that was
        working perfectly.
        """
        if answered.get("ok") is True:
            return answered.get("result")

        code = str(answered.get("error") or wire.INTERNAL)
        detail = str(answered.get("detail") or "")
        if code == wire.JOB_FAILED:
            raise JobFailed(detail, egress_gone=answered.get("egress_gone"))
        if code == wire.NETWORK_UNAVAILABLE:
            raise CouldNotRestrictTheNetwork(detail)
        if code == wire.RUNTIME_ABSENT:
            # The worker ANSWERED. That its host has no runtime is a fact it established, not an
            # absence of information.
            raise NoRuntimeThere(
                "the sandbox worker at " + self.address + " has no container runtime it can use"
                + (": " + detail if detail else ""))
        # Everything else is the worker refusing to have an opinion: unauthenticated, stale,
        # replayed, oversized, unknown, malformed, internal. A client is told nobody knows.
        #
        # THE CODE IS CARRIED, not only the sentence. It used to be folded into the message and
        # lost, so a caller could read "refused the request (no_lease)" and still have nothing
        # to branch on. A lease that has lapsed is recoverable and an unauthenticated caller is
        # not; telling them apart needs the code as a value, not as prose.
        refused = WorkerUnreachable(
            "the sandbox worker at " + self.address + " refused the request (" + code + ")"
            + (": " + detail if detail else ""))
        refused.cause = code
        refused.detail = detail
        raise refused

    # ------------------------------------------------------------------ what it is

    #: Methods that carry or act on work. None of them is sent before the two sides have been
    #: shown to speak a wire version both have been tested against.
    JOB_BEARING = ("run", "stop", "gone", "measure", "measure_egress")

    def _describe(self, *, wait: float = QUICK_SECONDS) -> dict:
        # `wait` is the CALLER'S budget when a job triggered this, and QUICK_SECONDS when
        # somebody asked for a description in its own right. A handshake that outlasts the job
        # that needed it turns the job's own bound into a fiction.
        if self._described is None:
            got = self._ask("describe", {}, wait=wait)
            if not isinstance(got, dict):
                raise WorkerUnreachable("the sandbox worker did not describe itself")
            self._agreed = self._agree_on_a_version(got)
            # WHETHER TO TAKE A LEASE, from the worker rather than from a switch nobody threw.
            # `lease_from_the_worker()` was the only thing that set `_leasing`, and it had no
            # caller anywhere -- not in the product, not in a test -- so every job-bearing call
            # to a worker that DOES keep leases was refused, correctly, with "this worker holds
            # no lease". A worker behind a unix socket on the gateway's own host keeps none and
            # says so, and then nothing is attached. Found on two machines, where the first
            # thing the control plane asked its worker for was a measurement.
            #
            # `.get(...)` and not `[...]`: a worker from before this field speaks a protocol
            # version we still accept, and for those the old behaviour -- no lease -- is right.
            self._leasing = bool(got.get("leases"))
            self._described = got
        return self._described

    def _agree_on_a_version(self, described: dict) -> str:
        """The newest version both sides have been TESTED against, or a refusal naming both.

        No downgrade and no guessing. A worker that names no range at all is one from before
        ranges existed; it is taken to speak the one version there was, which is the only
        reading that is not an assumption.
        """
        theirs = described.get("protocol_versions")
        theirs = tuple(str(v) for v in theirs) if isinstance(theirs, (list, tuple)) else (
            wire.PROTOCOL,)
        both = [v for v in wire.SUPPORTED if v in theirs]
        if not both:
            raise WorkerUnreachable(
                "this gateway has been tested against %s and the worker at %s reports %s. They "
                "share none, so nothing is sent: a version neither side has been tested against "
                "is not a version to fall back to. The gateway is %s and the worker is %s -- "
                "which build each side runs is a separate question from which wire version they "
                "speak, and it is not the reason for this refusal."
                % (", ".join(wire.SUPPORTED), self.address, ", ".join(theirs) or "nothing",
                   _build_identity(), str(described.get("build") or "an unnamed build")))
        return both[0]

    # `lease_from_the_worker()` used to sit here: public, documented as idempotent, and with no
    # caller anywhere in the product or the tests. That absence was the first lease defect two
    # machines found. It is gone rather than wired up, because `_leasing` is settled by the
    # handshake and `_hold_a_lease` is driven by work crossing -- a second, optional way in is
    # what let the property look present while being off. `Leases.renew` was callerless in the
    # same way and caused the second lease defect; dead code on this path has now cost twice.

    def _worth_retaking_the_lease(self, method: str, refused, *, already: bool) -> bool:
        """Is this the one refusal a fresh lease would answer? Named so it can be tested.

        Deliberately narrow. Only a job-bearing call, only on a worker that leases, only when
        the worker itself named `no_lease`, and only once. An unauthenticated caller, a stale
        replay and an oversized frame all arrive here too and none of them is fixed by taking
        a lease -- retrying those would turn a clear refusal into a loop.

        Safe for `run` in particular because the worker runs a job at most once, keyed on the
        request digest: the refused call did not start anything, and a resend under a new
        epoch cannot start it twice.
        """
        if already or method not in self.JOB_BEARING or not self._leasing:
            return False
        return getattr(refused, "cause", "") == wire.NO_LEASE

    def _forget_the_lease(self) -> None:
        """Drop a lease this side can no longer be sure of, so the next job takes a new one.

        `stop_leasing` clears the heartbeat but deliberately leaves `_leasing` off; this is the
        other half, and it is the half that was missing. `_lease_epoch` was assigned in exactly
        two places -- `None` at construction and the epoch at acquisition -- and nothing ever
        put it back. So once a lease lapsed, `_hold_a_lease` kept returning the dead number for
        the life of the process and every job was refused until somebody restarted the service.
        The heartbeat is stopped too: it is beating for an epoch that no longer exists, and the
        next acquisition starts a fresh one.
        """
        with self._lease_lock:
            self._lease_epoch = None
            if self._stop_beating is not None:
                self._stop_beating.set()
            self._heartbeat = None
            self._stop_beating = None

    def _hold_a_lease(self, *, wait: float = QUICK_SECONDS) -> int:
        # Bounded the same way and for the same reason: taking a lease is something a job made
        # this side do, so it may not outlast what that job was allowed.
        with self._lease_lock:
            if self._lease_epoch is None:
                said = self._ask("take_lease", {}, wait=wait)
                self._lease_epoch = int((said or {}).get("epoch") or 0)
                self._renew_within = float((said or {}).get("renew_within") or 5.0)
                self._start_the_heartbeat()
            return self._lease_epoch

    def _start_the_heartbeat(self) -> None:
        """Renew in the background, because a long job sends nothing for minutes and a lease
        that lapsed under a running container would have the worker stop it."""
        if self._heartbeat is not None:
            return
        import threading

        self._stop_beating = threading.Event()

        def beat():
            while not self._stop_beating.wait(max(0.5, self._renew_within)):
                try:
                    with self._lease_lock:
                        epoch = self._lease_epoch
                    if epoch is None:
                        return
                    self._ask("renew_lease", {"lease_epoch": epoch}, wait=QUICK_SECONDS)
                except Exception:                             # noqa: BLE001
                    # A missed beat is a busy machine or a blip. Several missed beats are what
                    # the worker acts on, and it acts on them by its own clock -- this side
                    # does not get to decide that its lease is still good.
                    pass

        self._heartbeat = threading.Thread(target=beat, name="worker-lease", daemon=True)
        self._heartbeat.start()

    def stop_leasing(self) -> None:
        """End the heartbeat. The lease lapses on the worker's own clock afterwards."""
        if self._stop_beating is not None:
            self._stop_beating.set()
        self._heartbeat = None
        self._leasing = False

    def close(self) -> None:
        """Let go of what this client holds. Idempotent, and never raises.

        On a socket that is the lease heartbeat and nothing else. `TlsWorker` extends it,
        because the TLS client also keeps a watch thread that re-reads the trust files.
        """
        try:
            self.stop_leasing()
        except Exception:                                     # noqa: BLE001 - going away anyway
            pass

    def agreed_protocol(self) -> str:
        """Which version the two sides settled on. Empty before they have spoken."""
        return self._agreed

    def instance_label(self) -> str:
        return str(self._describe().get("instance_label") or "")

    def boot_id(self) -> str:
        """The boot of the machine the worker is on -- not of the one asking."""
        return str(self._describe().get("boot_id") or "")

    def image_digest(self) -> str:
        return str(self._describe().get("image_digest") or "")

    def configuration_sha256(self) -> str:
        return str(self._describe().get("configuration_sha256") or "")

    def prove_its_ceilings(self, *, megabytes: int = 0, run_id: str = "") -> Ceilings:
        """Not this object's to answer.

        The runtime is on the worker's machine, and so is the only place an allocation can
        actually hit a ceiling. The worker process proves it there before it agrees to listen
        (`worker/service.py`), which is the point at which a failing proof can still refuse
        somebody's job. Answering True from here would be this side guessing about a machine it
        cannot see; answering False would refuse a worker that is enforcing perfectly well.
        """
        return Ceilings(
            held=None,
            reason=("the runtime is on the worker's machine, which proves its ceilings there "
                    "before it listens"),
            evidence={"worker_topology": self.topology})

    def runtime_version(self) -> str:
        return str(self._describe().get("runtime_version") or "")

    def can_it_isolate(self) -> Isolation:
        said = self._describe().get("isolation") or {}
        return Isolation(
            available=bool(said.get("available")),
            backend=str(said.get("backend") or "none"),
            reason=str(said.get("reason") or ""),
            measured=tuple(said.get("measured") or ()),
        )

    # ------------------------------------------------------------------ doing things

    def _note_who_ran(self, run_id: str, who) -> None:
        self._ran_on[run_id] = who
        while len(self._ran_on) > 10000:
            self._ran_on.popitem(last=False)

    def who_ran(self, run_id: str) -> tuple[str, str, str]:
        """(transport, identity, worker id) for the record. On a socket the identity is the
        address -- what the gateway knows it connected to -- and the id is the worker's own
        label, as it always was."""
        return self.transport, self.address, self.instance_label()

    def run(self, job: Job) -> Outcome:
        params = dict(job.as_message())
        params["artifact"] = wire.as_text(job.artifact)
        got = self._ask("run", {"job": params},
                        wait=float(job.limits.wall_clock_s) + self.run_margin,
                        run_id=job.run_id)
        if not isinstance(got, dict):
            raise WorkerUnreachable("the sandbox worker did not say what happened to the job")
        return Outcome(
            exit_code=got.get("exit_code"),
            stdout=str(got.get("stdout") or ""),
            stderr=str(got.get("stderr") or ""),
            reason=str(got.get("reason") or ""),
            native_status=got.get("native_status"),
            native_platform=str(got.get("native_platform") or ""),
            egress_gone=got.get("egress_gone"),
            runtime_platform=str(got.get("runtime_platform") or ""),
            # What the OTHER machine says it built and measured as this run's route out. Read with a
            # default, because a worker that predates it says nothing -- and a gateway that then
            # claimed an enforced allowlist would be claiming something nobody on that side reported.
            egress_record=(got.get("egress_record")
                           if isinstance(got.get("egress_record"), dict) else None),
        )

    def stop(self, run_id: str, container_name: str, appear_seconds: float) -> bool:
        return bool(self._ask("stop", {"run_id": run_id, "container_name": container_name,
                                       "appear_seconds": float(appear_seconds)},
                              wait=float(appear_seconds) + QUICK_SECONDS))

    def gone(self, container_name: str, patiently: bool = True) -> Gone:
        got = self._ask("gone", {"container_name": container_name, "patiently": bool(patiently)},
                        wait=QUICK_SECONDS * 2)
        if not isinstance(got, dict):
            raise WorkerUnreachable("the sandbox worker did not say what was left behind")
        return Gone(answered=bool(got.get("answered")), left=tuple(got.get("left") or ()))

    def measure(self, *, generated_at, options, egress_matrix, egress_expected):
        from dataclasses import asdict, is_dataclass

        return self._ask("measure", {
            "generated_at": generated_at,
            "options": asdict(options) if is_dataclass(options) else (options or None),
            "egress_matrix": egress_matrix,
            "egress_expected": list(egress_expected) if egress_expected else None,
        }, wait=MEASURE_SECONDS)

    def measure_egress(self, *, allowed, denied):
        return self._ask("measure_egress",
                         {"allowed": list(allowed) if not isinstance(allowed, str) else allowed,
                          "denied": denied}, wait=MEASURE_SECONDS)


class TlsWorker(SocketWorker):
    """The same line, over TCP with mutual TLS on loopback (`worker/tls.py`).

    Only `_open` differs. Every message still carries its MAC and every answer is still checked
    against its request -- a handshake that succeeded changes none of that.
    """

    transport = "mtls"

    def __init__(self, address: str, key: bytes, tls, connect_timeout: float = 10.0,
                 run_margin: float = RUN_MARGIN_SECONDS, say=None,
                 topology: str = SINGLE_HOST_DEVELOPMENT) -> None:
        import ssl

        from agentnode_sdk.pki import identity as _identity
        from agentnode_sdk.worker.tls import Contexts, Watch, endpoint

        # THE GATE, with the declaration it is judged against. A caller that names no topology
        # gets the loopback-only rule this transport has always had.
        endpoint(address, topology=topology)
        super().__init__(address, key, connect_timeout=connect_timeout, run_margin=run_margin,
                         topology=topology)
        self.tls = tls
        #: Where the gateway says what its TLS side did -- a renewed certificate taken up, an
        #: open connection cut. Flushed, for the same reason as the worker's (`worker/tls.py`).
        self.say = say or (lambda text: print(text, flush=True))
        self._contexts = Contexts(ssl.PROTOCOL_TLS_CLIENT, tls, say=self.say)
        #: The open connections to the worker, re-evaluated (decision 5.3): a worker revoked
        #: while a run is on its connection is cut, and the run ends as `transport_lost` with
        #: exactly one line -- the path an aborted run already takes.
        self.watch = Watch(tls, _identity.GATEWAY, say=self.say)

    def _open(self, budget: float | None = None):
        from agentnode_sdk.worker.tls import open_to_worker

        connection, who, der = open_to_worker(self.address, self.tls, self._contexts,
                                              self.connect_timeout, budget,
                                              topology=self._topology)
        _ask_the_kernel_to_notice_a_dead_peer(connection)
        # Watched until it is closed; a closed one is dropped at the next pass.
        self.watch.add(connection, der, who)
        return connection, who

    def close(self) -> None:
        """The lease heartbeat, and THE WATCH THREAD, which nothing used to stop.

        `Watch` starts on the first connection and re-reads the anchor, the revocation list and
        the floor every `reevaluate_seconds` for as long as it lives. In production the gateway
        IS the process, so a thread that outlives its client is invisible; in anything that
        makes a client and finishes with it -- a test, an embedded caller -- it goes on opening
        those three files forever. CI found it as file descriptors appearing and disappearing
        under a test about a gateway giving everything back.
        """
        super().close()
        try:
            self.watch.stop()
        except Exception:                                     # noqa: BLE001 - going away anyway
            pass

    def use_keyring(self, keyring, own_instance: str) -> None:
        """Authenticate frames with this pair's key instead of one key for everybody.

        Named rather than passed to `__init__` because it applies to the TLS transport only:
        selection needs an identity the handshake proved, and a unix socket has none.
        """
        self._keyring = keyring
        self._own_instance = str(own_instance)

    def instance_label(self) -> str:
        """The instance the worker's CERTIFICATE names, from the connection that answered the
        describe -- not the name it gave for itself in the answer."""
        self._describe()
        return str(getattr(self._described_by, "instance", "") or "")

    def who_ran(self, run_id: str) -> tuple[str, str, str]:
        """What the connection that carried this run proved. Empty when no such connection was
        made by this process -- a run interrupted before it reached the worker, or one this
        gateway did not start -- which the record says rather than filling in a guess."""
        who = self._ran_on.get(run_id)
        if who is None:
            return self.transport, "", ""
        return self.transport, who.uri(), who.instance


def from_address(address: str, key: bytes | None, tls=None, *,
                 topology: str = SINGLE_HOST_DEVELOPMENT,
                 keyring=None, own_instance: str = "") -> Worker:
    """The worker at this address. The only place that decides which transport is used.

    THE DECLARATION IS CHECKED FIRST, before any transport is chosen, so that a mismatch is a
    refusal rather than a connection to the wrong kind of place. Under `separate-worker-host`
    there is no unix socket and no in-process worker among the permitted address classes, which
    is what makes "no silent fallback" a fact about the code rather than a promise: there is
    nothing to fall back to, and a failure to reach the remote worker stays a failure.
    """
    # THE CALLER'S CONTRACT IS KEPT. Everything in the control plane that reaches a worker
    # catches `WorkerUnreachable`, and a refusal that arrived as a different type would escape
    # handling that is already written and already tested -- failing open in the one place that
    # must not. So a topology refusal is re-raised as one, carrying its cause and its step so
    # nothing is lost by the translation.
    try:
        _topology.check(topology, address, where="this gateway")
    except _topology.TopologyRefused as refused:
        unreachable = WorkerUnreachable(refused.because)
        unreachable.cause = refused.cause
        unreachable.what_to_do = refused.what_to_do
        raise unreachable from refused

    # A KEYRING REPLACES THE GLOBAL KEY; it does not sit beside it. Where one is supplied the
    # single shared key is never read and never used, which is the point of supplying it.
    if keyring is not None and not own_instance:
        raise WorkerUnreachable(
            "a keyring was given without the name this side answers to, and a pair key cannot "
            "be chosen without both names. This is a programming error, not a configuration "
            "one.")

    parsed = urlparse(address or "")
    if parsed.scheme in ("unix", "unix+stream"):
        return SocketWorker(address, key or b"", topology=topology)
    if parsed.scheme == "tcps":
        if tls is None:
            raise WorkerUnreachable(
                "%r asks for mutual TLS, and this gateway has no certificate settings for it. "
                "It is refused, not reached another way." % address[:60])
        worker = TlsWorker(address, key or b"", tls, topology=topology)
        if keyring is not None:
            worker.use_keyring(keyring, own_instance)
        return worker
    raise WorkerUnreachable(
        "this build reaches a worker over a unix socket or over mutual TLS, and "
        + repr(address[:60]) + " is neither.")


__all__ = ["SocketWorker", "TlsWorker", "from_address", "topology_of", "QUICK_SECONDS",
           "RUN_MARGIN_SECONDS", "struct"]
