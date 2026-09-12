"""A worker reached over a socket. Today that socket is always on this machine.

What decides where the worker is, is an address in the gateway's configuration and nothing else:
no product code names a path, an account or a host. That is what `ALPHA-BOUNDARY-0001` needs to
be true before the second machine exists, and it is true.

**It does not follow that the worker can be moved by changing that string, and an earlier version
of this docstring said it did.** `from_address` below speaks `unix://` and `unix+stream://` and
refuses every other scheme in as many words -- there is no `tcp://` here, and a worker on another
machine needs a transport that has not been written. Writing it is a change to the product, not
to a deployment. What IS established is that it would be the only thing to add: one function to
extend, with the vocabulary, the data, the failure modes and the record all indifferent to where
the other end is.

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
from urllib.parse import urlparse

from agentnode_sdk.worker import (
    Ceilings,
    NoRuntimeThere,
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

#: How long to wait for the answer to a question that is not a job. Connecting, describing,
#: stopping and looking are all quick or they are not happening.
QUICK_SECONDS = 60.0

#: What to add to a job's own wall clock before giving up on the worker. The job's limit is the
#: worker's to enforce; this is only how long the control plane waits for it to say so, and it is
#: longer so that "the sandbox stopped it at its limit" arrives instead of being cut off.
RUN_MARGIN_SECONDS = 120.0


def topology_of(address: str) -> str:
    """Where a worker reached at this address is, as far as the address can establish it.

    A unix socket does not leave the machine, so this is not a claim anybody made -- it is what
    the address IS. Loopback is the same: a connection to 127.0.0.1 is a connection to here.
    """
    parsed = urlparse(address or "")
    if parsed.scheme in ("unix", "unix+stream"):
        return SINGLE_HOST_DEVELOPMENT
    from agentnode_sdk.gateway.transport import is_loopback

    if is_loopback(parsed.hostname or ""):
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
                 run_margin: float = RUN_MARGIN_SECONDS) -> None:
        self.address = address
        self._key = key
        self.connect_timeout = connect_timeout
        #: How long past a job's OWN wall clock this gateway keeps waiting before it decides the
        #: worker has said nothing. It cannot be small: a worker running a job legitimately says
        #: nothing until the job is done, so the wait has to cover the job first. It is a
        #: parameter because how much slack a deployment allows is a property of the deployment
        #: -- and because a test cannot otherwise establish what happens after it elapses
        #: without waiting out the default.
        self.run_margin = run_margin
        self._described: dict | None = None

    # ------------------------------------------------------------------ the line itself

    @property
    def topology(self) -> str:                                # type: ignore[override]
        return topology_of(self.address)

    def _ask(self, method: str, params: dict, *, wait: float) -> object:
        """One question and its answer, or a refusal that says which kind it is."""
        deadline = time.time() + wait
        body = wire.request(method, params, deadline=deadline)
        if not hasattr(socket, "AF_UNIX"):
            raise WorkerUnreachable(
                "this machine has no unix sockets, so it cannot reach a worker over one. The "
                "gateway and its worker run on Linux; a client does not need either.")
        try:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(self.connect_timeout)
            connection.connect(_path_of(self.address))
        except OSError as exc:
            raise WorkerUnreachable(
                "the sandbox worker at " + self.address + " could not be reached: " + str(exc)
            ) from exc
        try:
            connection.settimeout(wait)
            connection.sendall(wire.seal(body, self._key))
            with connection.makefile("rb") as stream:
                answered = wire.read_frame(stream, self._key)
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
        return self._interpret(answered)

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
        raise WorkerUnreachable(
            "the sandbox worker at " + self.address + " refused the request (" + code + ")"
            + (": " + detail if detail else ""))

    # ------------------------------------------------------------------ what it is

    def _describe(self) -> dict:
        if self._described is None:
            got = self._ask("describe", {}, wait=QUICK_SECONDS)
            if not isinstance(got, dict):
                raise WorkerUnreachable("the sandbox worker did not describe itself")
            self._described = got
        return self._described

    def instance_label(self) -> str:
        return str(self._describe().get("instance_label") or "")

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

    def run(self, job: Job) -> Outcome:
        params = dict(job.as_message())
        params["artifact"] = wire.as_text(job.artifact)
        got = self._ask("run", {"job": params},
                        wait=float(job.limits.wall_clock_s) + self.run_margin)
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
        }, wait=1800.0)

    def measure_egress(self, *, allowed, denied):
        return self._ask("measure_egress",
                         {"allowed": list(allowed) if not isinstance(allowed, str) else allowed,
                          "denied": denied}, wait=1800.0)


def from_address(address: str, key: bytes) -> Worker:
    """The worker at this address. The only place that decides which transport is used."""
    parsed = urlparse(address or "")
    if parsed.scheme in ("unix", "unix+stream"):
        return SocketWorker(address, key)
    raise WorkerUnreachable(
        "this build knows how to reach a worker over a unix socket and nothing else yet. "
        + repr(address[:60]) + " would need a transport that is not here.")


__all__ = ["SocketWorker", "from_address", "topology_of", "QUICK_SECONDS", "RUN_MARGIN_SECONDS",
           "struct"]
