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


class Bench:
    """One worker, serving one socket, for one account.

    Deliberately not a class with a `start()` that returns: `serve_forever` is the process, and a
    worker that had a lifecycle of its own would be a worker that could be running when nothing
    started it.
    """

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
        self._serving = False

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
        self._socket = listener
        return path

    def serve_forever(self) -> None:
        if self._socket is None:
            self.open()
        self._serving = True
        while self._serving:
            try:
                connection, _ = self._socket.accept()
            except OSError:
                if self._serving:                             # pragma: no cover - a real error
                    continue
                return
            threading.Thread(target=self._one, args=(connection,), daemon=True).start()

    def stop_serving(self) -> None:
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
        # Per connection and never on `self`: a field would be one thread's request id answered
        # to another thread's caller.
        asked = ""
        try:
            if self.only_uid is not None:
                uid = self.who_is_connecting(connection)
                if uid != self.only_uid:
                    # Closed without a word. Telling an account it is the wrong account is
                    # telling it there is a right one.
                    return
            connection.settimeout(wire.FRESHNESS_SECONDS)
            with connection.makefile("rb") as stream:
                body = wire.read_frame(stream, self.key)
            asked = str(body.get("request_id") or "")
            wire.check(body, self.seen, floor=self.floor)
            result = self.answer(str(body["method"]), dict(body["params"]))
            connection.sendall(wire.seal(wire.answer(asked, result), self.key))
        except wire.ProtocolError as exc:
            self._refuse(connection, asked, exc.code, exc.detail)
        except CouldNotRestrictTheNetwork as exc:
            self._refuse(connection, asked, wire.NETWORK_UNAVAILABLE, str(exc))
        except JobFailed as exc:
            self._refuse(connection, asked, wire.JOB_FAILED, str(exc),
                         egress_gone=exc.egress_gone)
        except Exception as exc:                              # noqa: BLE001
            # Something here broke. Saying so is the point: a worker that hung instead would make
            # the control plane wait out its deadline for a failure it could have been told about.
            self._refuse(connection, asked, wire.INTERNAL, type(exc).__name__ + ": " + str(exc))
        finally:
            try:
                connection.close()
            except OSError:                                   # pragma: no cover
                pass

    def _refuse(self, connection, asked: str, code: str, detail: str = "",
                egress_gone=None) -> None:
        # A message that could not be authenticated or read is not answered at all. Replying to
        # one would tell whoever sent it which part of the shape was right, and there is no
        # request id to answer about anyway.
        if code in (wire.UNAUTHENTICATED, wire.TOO_LARGE, wire.MALFORMED) or not asked:
            return
        body = wire.refusal(asked, code, detail)
        if egress_gone is not None:
            body["egress_gone"] = egress_gone
        try:
            connection.sendall(wire.seal(body, self.key))
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
                "instance_label": self.worker.instance_label(),
                "image_digest": self.worker.image_digest(),
                "configuration_sha256": self.worker.configuration_sha256(),
            }
        if method == "run":
            return self.worker.run(self._job(params.get("job"))).as_message()
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


def serve(address: str, key_path: str, only_uid: int | None, worker=None) -> None:
    """Start a worker on this machine and answer until something stops the process."""
    from agentnode_sdk.sandbox.container_backend import ContainerBackend
    from agentnode_sdk.worker.local import LocalWorker

    if only_uid is None:
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

    # Next to the worker's own state, which is the only directory it may write. A worker that
    # could not write this would still refuse replays within its own life; it just could not
    # refuse one that spans a restart, so a failure here is a narrowing and not an opening.
    remembers_at = os.path.join(
        os.environ.get("HOME", "") or os.path.expanduser("~"), "replay-floor.json")
    bench = Bench(the_worker, address, wire.read_key(key_path), only_uid,
                  remembers_at=remembers_at)

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
    path = bench.open()
    print("  listening at " + path + " for uid " + str(only_uid))
    print("  this worker holds no pairing state, no signing identity and no client's token.")
    print("  On one host, two accounts are not isolation: see ALPHA-BOUNDARY-0001.")
    print("  a ceiling was hit here before this socket opened, and it held.")
    print("  it can also write down what it accepts, which is what refuses a replay after a "
          "restart.")
    bench.serve_forever()


def _time_now() -> float:                                     # pragma: no cover - a seam for tests
    return time.time()
