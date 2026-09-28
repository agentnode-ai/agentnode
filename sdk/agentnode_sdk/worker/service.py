"""The worker's end: a socket one account may reach, and a closed list of things it may ask.

This process is the only one that touches a container runtime. It holds no pairing state, no
signing identity, no client's token and no ledger, and it has no way to ask for any of them --
there is no method that names a file.

## Who may connect

Two checks, and the first is the kernel's. `SO_PEERCRED` says which account is on the other end of
a unix socket; it is not something the connecting side asserts, so it cannot be got wrong by
believing somebody. A connection from any account but the one this worker was started for is
closed without a word, because an error message is a thing to learn from.

The second is the message's own MAC. On this host that is belt and braces; it is here because it
is what carries over when the worker moves to another machine and there is no peer credential to
ask for.

## What this does not establish

Two accounts on one kernel are not isolation. This account can drive a container runtime, which on
a machine where that means the `docker` group means it is root-equivalent -- so a sandbox escape
here reaches the host, and the host is where the control plane is. That is exactly what
`ALPHA-BOUNDARY-0001` decided against and exactly what the second machine fixes. Until then this
is a development arrangement and says so.
"""
from __future__ import annotations

import os
import socket
import struct
import threading
import time

from agentnode_sdk.worker import (
    CouldNotRestrictTheNetwork,
    Job,
    JobFailed,
    Limits,
)
from agentnode_sdk.worker import protocol as wire
from agentnode_sdk.worker import journal as _journal
from agentnode_sdk.worker import lease as _lease
from agentnode_sdk.worker import topology as _topology

#: What a kernel calls the question "who is at the other end of this socket". Named here with
#: Linux's number rather than read off the socket module, because a platform that does not know
#: the name is a platform where asking fails -- and a worker that cannot establish who is
#: connecting must refuse to answer rather than serve anybody. The call below is what decides
#: that, on the real socket, at the moment it matters.
SO_PEERCRED = getattr(socket, "SO_PEERCRED", 17)

#: The permissions the socket is given. Owner and group, and nobody else: the group is what the
#: control plane's account is in, and it is the only other account that may say anything here.
SOCKET_MODE = 0o660

#: And the directory it sits in. A socket with careful permissions inside a directory anybody can
#: write to is a socket anybody can replace.
#: Owner and group only -- and SETGID, which is the part that matters and is easy to miss.
#:
#: A unix socket is created with the primary group of whatever process binds it. The worker's
#: primary group cannot be the group it shares with the gateway: rootless podman needs the
#: account's real passwd group, and `newuidmap` refuses outright when the process's gid is
#: anything else ("Target process is owned by a different user"). So the socket cannot get the
#: shared group from the process, and it has to come from the directory.
#:
#: Setgid on the directory is what does that: the socket bound inside it inherits the directory's
#: group rather than the binder's. The deployment creates the directory owned by the worker with
#: the shared group; this keeps the bit, so the socket the gateway has to reach carries it too.
DIRECTORY_MODE = 0o2750


#: How long the accept loop waits before looking at whether it has been told to stop. It is the
#: longest a `stop_serving()` can take to be noticed, so it is short; it is also a wake-up per
#: interval on an idle worker, so it is not shorter than it needs to be.
LOOK_UP_EVERY_SECONDS = 1.0


def _own_boot_id() -> str:
    """This machine's boot identity, or empty when it has none to give."""
    try:
        from agentnode_sdk.gateway.boot import boot_identity

        value, _how = boot_identity()
        return str(value or "")
    except Exception:                                         # noqa: BLE001 - never worth failing
        return ""


def reconcile_what_was_left(bench, *, say=None) -> dict:
    """What this worker left behind when it stopped, correlated with what it was asked to do.

    The sweep it replaces removed every container carrying one of this SDK's prefixes and
    reported three lists of NAMES. That is the right action and the wrong record: a name is not
    a run, so the gateway could not be told which run had been cleaned up, and the distinction
    between "cleaned", "could not be cleaned" and "nothing was there" was lost at the moment it
    mattered most.

    This correlates them. For every journal record that is not settled:

      * `accepted` -- claimed and never started, so nothing ran and nothing is to be cleaned.
        Settled as `never_started`, which is a DIFFERENT answer from `unknown` and costs the
        customer a different amount.
      * `started` -- it began. Its outcome stays unknown forever, and what can still be settled
        is the container: removed and proven gone, or not proven, recorded as itself.
    """
    said = say or (lambda text: None)
    if bench.journal is None:
        return {"considered": 0}
    settled = {"never_started": 0, "cleaned": 0, "unproven": 0, "considered": 0}
    for run_id, state, container in bench.journal.unsettled():
        settled["considered"] += 1
        if state == _journal.ACCEPTED:
            bench.journal.note_never_started(run_id)
            settled["never_started"] += 1
            said("  run %s was claimed and never started" % run_id)
            continue
        verified = None
        if container:
            try:
                bench.worker.stop(run_id, container, 0.0)
            except Exception:                                 # noqa: BLE001
                pass
            try:
                verified = bench.worker.gone(container, patiently=False).verified
            except Exception:                                 # noqa: BLE001
                verified = None
        bench.journal.note_cleanup(run_id, verified)
        settled["cleaned" if verified is True else "unproven"] += 1
        said("  run %s was interrupted; its sandbox is %s"
             % (run_id, "gone" if verified is True else "not provably gone"))
    return settled


class LeaseWatch:
    """Ends foreign code when the control plane that asked for it has gone.

    The lease stops NEW work by itself: nothing without a live lease is accepted. That leaves
    the case this exists for -- work that was already running when the lease lapsed. Without
    this, a worker that lost its control plane would keep running somebody's code until the
    job's own wall clock, which may be an hour, with nothing able to stop it: the operator's
    kill switch, an account suspension and a revoked device are all things the CONTROL PLANE
    acts on, and it is the control plane that is gone.

    So the bound is the lease, and it is the worker's own clock that enforces it.

    What happens on a lapse is decided rather than left to chance: the run is stopped, cleanup
    is attempted, and whether cleanup could be ESTABLISHED is written down as itself. "I could
    not prove the sandbox is gone" is not "the sandbox is gone", and it is not "it is still
    there" either.
    """

    def __init__(self, bench, *, every: float = 1.0, say=None) -> None:
        self.bench = bench
        self.every = float(every)
        self.say = say or (lambda text: None)
        self._stop = None
        self._thread = None

    def start(self) -> None:
        import threading

        if self._thread is not None or self.bench.leases is None:
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="lease-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self.every):
            try:
                self.once()
            except Exception as broke:                        # noqa: BLE001 - never die quietly
                self.say("  the lease watch itself failed: %r" % (broke,))

    def once(self) -> int:
        """One pass. Returns how many runs were ended. Separate from the loop so a test can
        see the decision rather than whatever the loop has already done to it."""
        leases = self.bench.leases
        if leases is None or leases.lapsed() is None:
            return 0
        ended = 0
        for run_id, container in self.bench.in_flight():
            ended += 1
            self.say("  the lease lapsed; ending run %s" % run_id)
            try:
                self.bench.worker.stop(run_id, container, _lease.STOP_APPEAR_SECONDS)
            except Exception:                                 # noqa: BLE001
                pass
            verified = None
            try:
                verified = self.bench.worker.gone(container, patiently=False).verified
            except Exception:                                 # noqa: BLE001
                verified = None
            if self.bench.journal is not None:
                try:
                    self.bench.journal.note_cleanup(run_id, verified)
                except Exception:                             # noqa: BLE001
                    pass
            self.bench.forget_in_flight(run_id)
        # The lease is given up only after the work it covered has been dealt with, so a
        # takeover cannot find the worker idle while a container is still going.
        leases.release()
        return ended


def _build_identity() -> str:
    """Which code this is. Diagnostic only: it never decides whether two sides may speak."""
    try:
        from agentnode_sdk import __version__

        return "agentnode-sdk/%s" % __version__
    except Exception:                                         # noqa: BLE001 - never worth failing
        return "agentnode-sdk/unknown"


def _sealing(chosen, fallback) -> bytes:
    """Which key an ANSWER is sealed with. During a rotation overlap a request may arrive under
    either key; the answer always goes back under the current one, which is the first of them.
    Sealing with the retired key would keep the overlap alive from this side."""
    if chosen is None:
        return fallback
    return chosen if isinstance(chosen, (bytes, bytearray)) else tuple(chosen)[0]


class Bench:
    """One worker, serving one socket, for one account.

    Deliberately not a class with a `start()` that returns: `serve_forever` is the process, and a
    worker that had a lifecycle of its own would be a worker that could be running when nothing
    started it.
    """

    #: Where this worker writes down what it has been asked to do. `None` on the single-host
    #: arrangement, where one gateway opens one connection per request and never retries.
    journal = None

    #: Who may give this worker work, and under which epoch. `None` on the single-host
    #: arrangement, where there is one control plane and nothing to fence against.
    leases = None

    #: The caller the current connection proved itself to be. Set by the TLS door before the
    #: conversation; empty on the socket, where the kernel's account check stands in for it.
    _caller = ""

    def __init__(self, worker, address: str, key: bytes, only_uid: int | None,
                 remembers_at=None) -> None:
        self.worker = worker
        self.address = address
        self.key = key
        #: Which account may speak here. None means every account may, which is refused at
        #: startup rather than here: a worker with no answer to "who may connect" is not one to
        #: start.
        self.only_uid = only_uid
        self.seen = wire.Seen()
        #: What this worker will not go back before, kept across restarts. `Seen` forgets when
        #: the process does -- a monotonic clock starts again with it -- so on its own it leaves
        #: "capture, wait for a restart, set the clock back, send again" open. This is the part
        #: that does not forget.
        self.floor = wire.Floor(remembers_at)
        self._socket: socket.socket | None = None
        #: What this worker calls itself when it holds a certificate: the instance in it.
        #: None when it does not, and then the worker's own label is used as before.
        self.label: str | None = None
        #: Set once, never cleared. Separate from `_serving` so a stop cannot be undone by
        #: a loop that starts afterwards.
        self._stopped = False
        self._serving = False
        #: run_id -> container name, for work this worker has STARTED and not
        #: finished. The lease watch ends these when the control plane that asked
        #: for them stops being able to.
        self._in_flight: dict[str, str] = {}
        import threading as _threading

        self._in_flight_lock = _threading.Lock()

    # ------------------------------------------------------------------ the socket

    def open(self) -> str:
        from agentnode_sdk.worker.remote import _path_of

        path = _path_of(self.address)
        if not path:
            raise ValueError("a worker needs somewhere to listen")
        folder = os.path.dirname(path) or "."
        os.makedirs(folder, exist_ok=True)
        try:
            os.chmod(folder, DIRECTORY_MODE)
        except OSError:                                       # pragma: no cover - not ours to fix
            pass
        # A socket file left by a process that is gone is not a worker; a socket file whose
        # process is alive is. Binding decides which: bind fails on a live one.
        if os.path.exists(path):
            try:
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                probe.settimeout(1.0)
                probe.connect(path)
                probe.close()
                raise OSError("a worker is already listening at " + path)
            except ConnectionRefusedError:
                os.unlink(path)
            except FileNotFoundError:                         # pragma: no cover - raced away
                pass
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(path)
        # The mode goes on before anything is listening, so there is no instant in which the
        # socket exists and anyone may connect to it.
        os.chmod(path, SOCKET_MODE)
        listener.listen(16)
        # A listening socket that a thread is already blocked in accept() on does NOT wake when
        # another thread closes it: the close succeeds and the blocked thread stays blocked until
        # somebody connects. So `stop_serving()` could clear the flag, close the socket, and the
        # worker would keep serving -- which is a worker that cannot be stopped, and six of them
        # were found still in accept() after a whole test session had ended.
        #
        # A timeout makes the loop come up for air and look at the flag. The accepted connection
        # is unaffected: accept() hands back a BLOCKING socket when the listener has a timeout.
        listener.settimeout(LOOK_UP_EVERY_SECONDS)
        self._socket = listener
        return path

    def serve_forever(self) -> None:
        """Accept connections until told to stop, and notice being told within a second.

        A stop that arrived BEFORE this started is honoured rather than overwritten. This
        used to set the serving flag unconditionally, so `stop_serving()` racing a worker
        that was still starting was simply lost and the worker served on -- a worker told
        to stop that does not. The stop is its own flag now, and setting it is one-way.
        """
        if self._stopped:
            return
        if self._socket is None:
            self.open()
        self._serving = True
        while self._serving and not self._stopped:
            try:
                connection, _ = self._socket.accept()
            except OSError:
                if self._serving:                             # pragma: no cover - a real error
                    continue
                return
            threading.Thread(target=self._one, args=(connection,), daemon=True).start()

    def stop_serving(self) -> None:
        """Told once, stopped for good. Safe to call before serving has begun."""
        self._stopped = True
        self._serving = False
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:                                   # pragma: no cover
                pass

    # ------------------------------------------------------------------ one connection

    def who_is_connecting(self, connection) -> int | None:
        """The account at the other end, as the kernel reports it. None where it cannot be asked."""
        try:
            raw = connection.getsockopt(socket.SOL_SOCKET, SO_PEERCRED, struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", raw)
        except (OSError, AttributeError, struct.error):
            # Not "anybody may connect". Nobody may: a worker that cannot be told which account
            # is speaking has no way to serve one account rather than every account.
            return None
        return uid

    def _one(self, connection) -> None:
        if self.only_uid is not None:
            uid = self.who_is_connecting(connection)
            if uid != self.only_uid:
                # Closed without a word. Telling an account it is the wrong account is
                # telling it there is a right one.
                try:
                    connection.close()
                except OSError:                               # pragma: no cover
                    pass
                return
        self.converse(connection)

    def converse(self, connection, noted=None, key=None) -> None:
        """Everything after the door: MAC, nonce, floor, the closed list, the answer.

        The same for both doors. The unix socket reaches it after the kernel has named the
        account; the TLS listener (`worker/tls.py`) reaches it after the handshake and the
        identity checks. Neither door skips anything in here because of what it checked first.

        `noted`, when given, is told the method and parameters of a request that PASSED every
        check here, before it is answered -- the TLS door uses it to know which run a connection
        carries, so that a connection cut because its caller was revoked also stops that run.
        """
        # Per connection and never on `self`: a field would be one thread's request id answered
        # to another thread's caller.
        asked = ""
        try:
            connection.settimeout(wire.FRESHNESS_SECONDS)
            with connection.makefile("rb") as stream:
                body = wire.read_frame(stream, key if key is not None else self.key)
            asked = str(body.get("request_id") or "")
            wire.check(body, self.seen, floor=self.floor)
            if noted is not None:
                noted(str(body["method"]), dict(body["params"]))
            result = self.answer(str(body["method"]), dict(body["params"]))
            connection.sendall(wire.seal(wire.answer(asked, result), _sealing(key, self.key)))
        except wire.ProtocolError as exc:
            self._refuse(connection, asked, exc.code, exc.detail, key=key)
        except CouldNotRestrictTheNetwork as exc:
            self._refuse(connection, asked, wire.NETWORK_UNAVAILABLE, str(exc), key=key)
        except JobFailed as exc:
            self._refuse(connection, asked, wire.JOB_FAILED, str(exc), key=key,
                         egress_gone=exc.egress_gone)
        except Exception as exc:                              # noqa: BLE001
            # Something here broke. Saying so is the point: a worker that hung instead would make
            # the control plane wait out its deadline for a failure it could have been told about.
            self._refuse(connection, asked, wire.INTERNAL,
                         type(exc).__name__ + ": " + str(exc), key=key)
        finally:
            try:
                connection.close()
            except OSError:                                   # pragma: no cover
                pass

    def _refuse(self, connection, asked: str, code: str, detail: str = "",
                egress_gone=None, key=None) -> None:
        # A message that could not be authenticated or read is not answered at all. Replying to
        # one would tell whoever sent it which part of the shape was right, and there is no
        # request id to answer about anyway.
        if code in (wire.UNAUTHENTICATED, wire.TOO_LARGE, wire.MALFORMED) or not asked:
            return
        body = wire.refusal(asked, code, detail)
        if egress_gone is not None:
            body["egress_gone"] = egress_gone
        try:
            connection.sendall(wire.seal(body, _sealing(key, self.key)))
        except OSError:                                       # pragma: no cover
            pass

    # ------------------------------------------------------------------ the closed list

    def answer(self, method: str, params: dict):
        """Every method there is. A name that is not here never reaches anything."""
        if method == "describe":
            isolation = self.worker.can_it_isolate()
            return {
                "isolation": isolation.as_message(),
                "runtime_version": self.worker.runtime_version(),
                # A worker that holds a certificate calls itself by the instance in it, through
                # either door. What a gateway records over TLS does not rest on this answer --
                # it is the identity checked in the handshake -- but the two agree.
                "instance_label": self.label or self.worker.instance_label(),
                "image_digest": self.worker.image_digest(),
                "configuration_sha256": self.worker.configuration_sha256(),
                # TWO SEPARATE FACTS. The range is what this worker has been TESTED to speak;
                # the build is which code it is. A gateway refuses on the first and reports the
                # second, so that a security update to one host does not need the other to be
                # updated in lockstep to keep working.
                "protocol_versions": list(wire.SUPPORTED),
                "build": _build_identity(),
                # WHICH BOOT OF THIS MACHINE. The report the gateway binds describes what a
                # container gets HERE, and that changes across a reboot of this host -- not of
                # the gateway's. On one machine the two were the same value; on two they are
                # two different facts, and only this one is about the measurement.
                "boot_id": _own_boot_id(),
            }
        if method == "take_lease":
            held = self.leases.take(self._caller)
            return {"holder": held.holder, "epoch": held.epoch,
                    "renew_within": _lease.HEARTBEAT_EVERY_SECONDS,
                    "lapses_after": _lease.LEASE_SECONDS}
        if method == "renew_lease":
            held = self._with_lease(params)
            return {"holder": held.holder, "epoch": held.epoch,
                    "renew_within": _lease.HEARTBEAT_EVERY_SECONDS,
                    "lapses_after": _lease.LEASE_SECONDS}
        if method == "run":
            job = self._job(params.get("job"))
            # IMMEDIATELY BEFORE THE CONTAINER, not only when the request arrived. A lease that
            # was alive when this message landed may have lapsed while the job was being read,
            # and starting foreign code for a control plane that has since gone is the thing
            # the lease exists to prevent.
            self._with_lease(params)
            return self._run_at_most_once(job)
        if method == "result":
            return self._result(str(params.get("run_id") or ""),
                                acknowledge=bool(params.get("acknowledge")))
        if method in ("stop", "gone", "measure", "measure_egress"):
            self._with_lease(params)
        if method == "stop":
            return bool(self.worker.stop(
                str(params.get("run_id") or ""), str(params.get("container_name") or ""),
                float(params.get("appear_seconds") or 0.0)))
        if method == "gone":
            return self.worker.gone(str(params.get("container_name") or ""),
                                    bool(params.get("patiently", True))).as_message()
        if method == "measure":
            from agentnode_sdk.conformance.runner import SuiteOptions

            said = params.get("options")
            report = self.worker.measure(
                generated_at=str(params.get("generated_at") or ""),
                options=SuiteOptions(**said) if isinstance(said, dict) else None,
                egress_matrix=params.get("egress_matrix"),
                egress_expected=params.get("egress_expected"))
            return report if isinstance(report, dict) else report.to_dict()
        if method == "measure_egress":
            return self.worker.measure_egress(allowed=params.get("allowed"),
                                              denied=params.get("denied"))
        raise wire.ProtocolError(wire.UNKNOWN_METHOD, method)  # pragma: no cover - `check` first

    def in_flight(self):
        """(run_id, container_name) for every run this worker has started and not finished.
        A copy, because the caller ends them and that changes the registry."""
        with self._in_flight_lock:
            return list(self._in_flight.items())

    def forget_in_flight(self, run_id: str) -> None:
        with self._in_flight_lock:
            self._in_flight.pop(str(run_id), None)

    def _with_lease(self, params: dict):
        """The lease this instruction is covered by, or a refusal naming which way it failed.

        Only enforced where a lease exists to enforce: the single-host arrangement has one
        control plane, one connection per request and no takeover to fence against, and giving
        it a lease would be ceremony rather than protection.
        """
        if self.leases is None:
            return None
        try:
            return self.leases.check(self._caller, params.get("lease_epoch"))
        except _lease.LeaseRefused as refused:
            raise wire.ProtocolError(
                wire.NO_LEASE, refused.because + " " + refused.what_to_do) from refused

    # ------------------------------------------------------------------ at most once

    def _run_at_most_once(self, job: Job):
        """The only path that starts a container, and the only one that decides to.

        Without a journal this is what it always was -- one machine, one gateway, one
        connection per request, no retries -- and the caller gets the old behaviour. With one,
        the decision and the record of the decision are a single act: the record is created by
        a link() that fails if the name exists, so of any number of simultaneous deliveries of
        the same run id exactly one proceeds and the rest are told what became of it.
        """
        if self.journal is None:
            return self.worker.run(job).as_message()

        from agentnode_sdk.worker import journal as _journal

        try:
            claim = self.journal.claim(job.run_id, _journal.digest_of_job(job),
                                       container=job.container_name)
        except _journal.JournalRefused as refused:
            # FAIL CLOSED. A worker that cannot write down what it is about to do could do it
            # again, so it does not do it at all.
            raise wire.ProtocolError(
                wire.RUN_ID_CONFLICT if refused.cause == "run_id_reused_for_different_work"
                else wire.JOURNAL_REFUSED,
                refused.because + " " + refused.what_to_do) from refused

        if claim.verdict == _journal.DID_NOT_RUN:
            # Claimed once and never started -- the worker died between the two. Settled, and
            # settled as "it did not run", which is not the same as "nobody knows": that
            # distinction is the difference between billing it and not.
            raise wire.ProtocolError(
                wire.OUTCOME_UNKNOWN,
                "run %s was claimed on this worker and never started, and it has been settled "
                "that way. It is not started now: the run id has been used." % job.run_id)
        if claim.verdict == _journal.DONE:
            # Already run. The recorded outcome IS the answer -- re-running it to produce a
            # fresh one would be running foreign code twice to avoid reading a file.
            return claim.outcome
        if claim.verdict == _journal.IN_FLIGHT:
            raise wire.ProtocolError(
                wire.ALREADY_RUNNING,
                "run %s is already running on this worker. It was not started a second time."
                % job.run_id)
        if claim.verdict == _journal.UNKNOWN:
            raise wire.ProtocolError(
                wire.OUTCOME_UNKNOWN,
                "run %s was started on this worker and how it ended was never written down. "
                "It is not started again: doing that could run it twice, and of the two, twice "
                "is worse than not knowing." % job.run_id)
        if not claim.may_execute:                             # pragma: no cover - all covered
            raise wire.ProtocolError(wire.JOURNAL_REFUSED, "the journal did not permit this run")

        # From here exactly one caller is running exactly this job.
        self.journal.note_started(job.run_id)
        with self._in_flight_lock:
            self._in_flight[str(job.run_id)] = str(job.container_name)
        try:
            outcome = self.worker.run(job).as_message()
        except BaseException:
            # It began and it did not produce an outcome. The record stays at `started`, which
            # is what `unknown` looks like from outside, and is not a licence to run it again.
            raise
        finally:
            self.forget_in_flight(job.run_id)
        self.journal.note_finished(job.run_id, outcome)
        return outcome

    def _result(self, run_id: str, *, acknowledge: bool = False):
        """What became of a run, for a control plane that lost the answer.

        Never runs anything and never invents anything: an outcome it does not have is reported
        as not had. This is the method that turns "the connection dropped after it ran" from a
        permanent unknown into a fact.
        """
        if self.journal is None:
            raise wire.ProtocolError(
                wire.JOURNAL_REFUSED,
                "this worker keeps no journal, so it cannot say what became of an earlier run.")
        from agentnode_sdk.worker import journal as _journal

        if acknowledge:
            # The control plane has written its own line. Letting the record go is the only
            # thing this does; it never changes what the record SAYS.
            try:
                self.journal.acknowledge(run_id)
            except _journal.JournalRefused:
                pass
        known = self.journal.look(run_id)
        if known is None:
            return {"known": False, "state": "", "outcome": None, "cleanup": None,
                    "ran_for": None, "never_ran": False}
        return {"known": True, "state": known.state,
                # SETTLED AS NOT HAVING RUN. Reported separately from an unknown outcome
                # because the two cost different amounts: one is billed and one is not.
                "never_ran": known.verdict == _journal.DID_NOT_RUN,
                "outcome": known.outcome if known.verdict == _journal.DONE else None,
                "cleanup": known.cleanup,
                "unknown_outcome": known.verdict == _journal.UNKNOWN,
                # HOW LONG IT ACTUALLY RAN, measured by the clock that ran it. A duration
                # rather than two timestamps, on purpose: the two machines' clocks are not the
                # same clock, and a difference between them is not a fact about either. The
                # gateway bills this duration from its own start, so time the connection spent
                # broken is not charged as execution.
                "ran_for": known.ran_for}

    @staticmethod
    def _job(said) -> Job:
        """A job, from what arrived, or a refusal that says the shape was wrong.

        Nothing here is trusted to be the right type because it was sent by something holding the
        key: a message that authenticates is a message from the control plane, not a message that
        is correct.
        """
        if not isinstance(said, dict):
            raise wire.ProtocolError(wire.BAD_PARAMS, "a run carries a job")
        command = said.get("command")
        if not isinstance(command, list) or not all(isinstance(a, str) for a in command):
            raise wire.ProtocolError(
                wire.BAD_PARAMS,
                "a job's command is a list of arguments. It is passed to the runtime as a list "
                "and is never joined into anything a shell would read")
        domains = said.get("allowed_domains") or []
        if not isinstance(domains, list) or not all(isinstance(d, str) for d in domains):
            raise wire.ProtocolError(wire.BAD_PARAMS, "allowed destinations are names")
        limits = said.get("limits")
        if not isinstance(limits, dict):
            raise wire.ProtocolError(wire.BAD_PARAMS, "a job carries its limits")
        try:
            return Job(
                run_id=str(said["run_id"]),
                container_name=str(said["container_name"]),
                command=tuple(command),
                artifact=wire.from_text(said.get("artifact") or ""),
                stdin=str(said.get("stdin") or ""),
                network=str(said.get("network") or "none"),
                allowed_domains=tuple(domains),
                limits=Limits(
                    cpu=float(limits.get("cpu", 1.0)),
                    memory_mb=int(limits.get("memory_mb", 512)),
                    processes=int(limits.get("processes", 256)),
                    wall_clock_s=int(limits.get("wall_clock_s", 60)),
                    storage_mb=int(limits.get("storage_mb", 0)),
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise wire.ProtocolError(wire.BAD_PARAMS, str(exc)) from exc


class CannotHoldItsLimits(RuntimeError):
    """Raised instead of listening, when a ceiling was not shown to bind.

    A separate exception rather than a message, so that the thing which starts a worker can tell
    "this host does not hold its limits" apart from "the socket path was wrong" -- they need
    different actions from an operator, and one of them means no job may be accepted here.
    """

    def __init__(self, reason: str, evidence: dict | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.evidence = dict(evidence or {})


def serve(address: str, key_path: str, only_uid: int | None, worker=None, *,
          tls_address: str = "", tls=None,
          topology: str = _topology.SINGLE_HOST_DEVELOPMENT, keyring_path: str = "",
          journal_at: str = "") -> None:
    """Start a worker on this machine and answer until something stops the process.

    `tls_address` and `tls` open the mutual-TLS door, TCP on loopback (`worker/tls.py`). Both or
    neither: an address without settings, or settings without an address, is refused rather than
    half-started.

    EITHER DOOR, OR BOTH, AND NEVER NEITHER. The socket used to be compulsory and the TLS door
    could only stand beside it, which meant a deployment that wanted mutual TLS had to leave a
    second way in that nothing was checking against the same rules. A worker asked for the TLS
    door alone now opens only that one, and no socket file is created; a worker asked for
    neither is refused, because a worker nobody can reach is not a worker.
    """
    if bool(tls_address) != bool(tls):
        raise ValueError(
            "a TLS listener needs both an address and its certificate settings; one without the "
            "other is refused rather than started without its checks")
    if not address and not tls:
        raise ValueError(
            "a worker needs a door: a unix socket, mutual TLS on loopback, or both. Started with "
            "neither it would hold a container runtime and answer nobody.")
    # BEFORE A RUNTIME IS TOUCHED. A worker for another machine that holds no per-pair key is
    # misconfigured, and finding that out after proving a memory ceiling wastes a minute and
    # buries the reason under other output.
    if topology == _topology.SEPARATE_WORKER_HOST and not journal_at:
        from agentnode_sdk.worker import journal as _journal

        raise _journal.JournalRefused(
            "journal_not_configured",
            "this worker was started for its own machine, and a worker reached over a network "
            "must be able to say whether it has already run a job. Retries and reconnects are "
            "ordinary there, and each carries a fresh nonce, so nothing else would stop the "
            "same job running twice.",
            "Start it with --journal <directory> on the worker's own disk.")
    if topology == _topology.SEPARATE_WORKER_HOST and not keyring_path:
        from agentnode_sdk.worker import pairkeys as _pairkeys

        raise _pairkeys.KeyringRefused(
            _pairkeys.NO_FILE,
            "this worker was started for its own machine, and a worker on its own machine does "
            "not authenticate its control plane with a key shared by everything.",
            "Start it with --keyring <path>, holding the key for this pair only.")
    from agentnode_sdk.sandbox.container_backend import ContainerBackend
    from agentnode_sdk.worker.local import LocalWorker

    # WHICH ACCOUNT MAY SPEAK -- and only where that question has an answer. `only_uid` is
    # checked with SO_PEERCRED, which reads the account at the other end of a UNIX SOCKET. A
    # worker whose only door is mutual TLS has no such peer: the caller is on another machine
    # and there is no local account to name. Requiring one there would be asking an operator to
    # invent an answer to a question nobody asks, and the thing that actually decides who may
    # speak on that door is the certificate, which is checked before a byte is read.
    #
    # So it is required exactly where it does something: when there is a unix socket.
    if address and only_uid is None:
        raise ValueError(
            "a worker is started for one account. Without one, anything that can reach the "
            "socket could ask it to run code, which is the thing the socket's permissions and "
            "this check exist to prevent.")
    the_worker = worker or LocalWorker(ContainerBackend())

    # Before it agrees to run anybody's code, it shows that a ceiling binds -- by hitting one.
    # A worker whose limits are quietly not applied is worse than one that is down: down is
    # visible, and a job that runs with no memory limit on a host that believes it has one is
    # not. So this refuses rather than warning, and there is deliberately no flag to skip it.
    proof = the_worker.prove_its_ceilings()
    if proof.held is False:
        raise CannotHoldItsLimits(proof.reason, proof.evidence)
    if proof.held is None:
        # Only a worker that is somewhere else answers this, and a worker that is somewhere else
        # is not the one being started here.
        raise CannotHoldItsLimits(
            "this worker could not say whether its ceilings bind, and a worker that cannot say "
            "is not one to hand foreign code to: " + (proof.reason or "no reason given"),
            proof.evidence)

    # WHAT THE PREVIOUS WORKER LEFT, before this one opens its socket. At this moment no job of
    # its own can be in flight, so anything carrying this SDK's prefixes is a leftover from a
    # worker that died mid-run -- which is now possible to leave behind, because the single-run
    # path no longer passes `--rm` and a container that removes itself cannot be asked how it
    # ended. Never fatal: it reports what it could not do rather than refusing to start.
    left = getattr(the_worker, "remove_what_a_previous_worker_left", None)
    if callable(left):
        swept = left()
        if swept.get("found"):
            print("  Removed %d container(s) a previous worker left: %s"
                  % (len(swept.get("removed") or []), ", ".join(swept.get("found") or [])))
        if swept.get("failed") or swept.get("why"):
            print("  Could not account for every leftover: %s%s"
                  % (", ".join(swept.get("failed") or []) or "-",
                     (" (" + swept["why"] + ")") if swept.get("why") else ""))

    # Next to the worker's own state, which is the only directory it may write. A worker that
    # could not write this would still refuse replays within its own life; it just could not
    # refuse one that spans a restart, so a failure here is a narrowing and not an opening.
    remembers_at = os.path.join(
        os.environ.get("HOME", "") or os.path.expanduser("~"), "replay-floor.json")
    bench = Bench(the_worker, address, wire.read_key(key_path), only_uid,
                  remembers_at=remembers_at)
    if journal_at:
        from agentnode_sdk.worker.journal import Journal

        bench.journal = Journal(journal_at)
        # The lease counter lives beside the journal: both are this worker's durable memory of
        # what it has been asked to do and by whom.
        bench.leases = _lease.Leases(os.path.join(journal_at, _lease.COUNTER_NAME))
        # And the thing that ends work when the control plane that asked for it stops being
        # entitled to have asked. Started here rather than inside `Leases` so that a test can
        # drive one pass of it without a thread.
        LeaseWatch(bench, say=print).start()
        # BEFORE THE DOOR OPENS. What this worker left behind last time is settled while
        # nothing new can arrive, so a gateway asking about an interrupted run gets an answer
        # rather than a record that is still being written.
        print("  reconciling what the previous worker left: %r"
              % (reconcile_what_was_left(bench, say=print),))

    # Before the socket, like the ceiling. What a worker has already accepted is what stops a
    # message captured earlier being replayed after a restart, and a worker that cannot record
    # that has no such protection -- while looking exactly like one that has. Proved by writing,
    # because a directory that looks writable and a file that can be written are different
    # questions and only the second one matters.
    try:
        bench.floor.can_be_written()
    except wire.CannotRememberTheFloor as exc:
        raise CannotHoldItsLimits(
            "this worker cannot keep a record of what it has accepted, so it cannot refuse a "
            "message captured before a restart: " + str(exc),
            {"replay_floor": remembers_at}) from exc
    path = bench.open() if address else ""
    listener = None
    if tls:
        from agentnode_sdk.worker.tls import TlsListener, own_instance

        bench.label = own_instance(tls)
        listener = TlsListener(bench, tls_address, tls, topology=topology)
        if keyring_path:
            from agentnode_sdk.worker import pairkeys as _pairkeys
            from agentnode_sdk.worker.tls import own_instance

            listener.use_keyring(_pairkeys.Keyring.read(keyring_path), own_instance(tls))
        host, port = listener.open()
        if address:
            threading.Thread(target=listener.serve_forever, daemon=True).start()
        # "also" only when there is something for it to be also to. A worker whose only door is
        # this one announced itself as though a socket were open beside it -- which on the closed
        # alpha, where the socket had deliberately been taken away, said the opposite of the truth.
        print("  %slistening with mutual TLS at %s:%s, loopback only, as %s"
              % ("also " if address else "", host, port, bench.label), flush=True)
        print("  it accepts gateway instance(s): " + ", ".join(sorted(tls.accept)), flush=True)
    if address:
        print("  listening at " + path + " for uid " + str(only_uid))
    else:
        print("  there is NO unix socket: this worker answers over mutual TLS and nothing else.")
    print("  this worker holds no pairing state, no signing identity and no client's token.")
    print("  On one host, two accounts are not isolation: see ALPHA-BOUNDARY-0001.")
    print("  a ceiling was hit here before this door opened, and it held.")
    print("  it can also write down what it accepts, which is what refuses a replay after a "
          "restart.")
    try:
        if address:
            bench.serve_forever()
        else:
            # Nothing is listening on a socket to block in, so the TLS door is served here
            # rather than in the thread above -- which is only started when there is a socket
            # to come back to.
            listener.serve_forever()
    finally:
        if listener is not None:
            listener.stop_serving()


def _time_now() -> float:                                     # pragma: no cover - a seam for tests
    return time.time()
