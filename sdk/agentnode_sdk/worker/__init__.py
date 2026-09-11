"""The only thing the control plane may ask of whatever runs foreign code.

`ALPHA-BOUNDARY-0001` decided that foreign code belongs on a different machine from the process
that holds every client's token material and this gateway's signing identity. That machine is not
being bought yet; the alpha is built on one host as a closed development environment. What has to
be true NOW is the thing that makes the later move a deployment change rather than a rewrite: no
behaviour of the product may depend on the worker being here.

So this module is a vocabulary and nothing else. Five questions, every one of them answerable by
something on another machine:

    can_it_isolate()      what runs code there, whether it can isolate it, and what was measured
    runtime_version()     which version of that, for the record to bind
    run(job)              run this artefact under this policy, and say what happened
    stop(run_id)          stop that run
    gone(run_id)          whether what it left behind is gone -- yes, no, or could not ask

Everything that crosses is data. There are no objects with methods on them, no file handles, no
process ids and no paths that only mean something where they were made: a `Job` can be written
down, sent, read back and run, and an `Outcome` can come back the same way. That is not a style
preference -- it is the property that makes `SocketWorker` possible without touching the gateway.

## What the worker is told, and what it is not

It is told the artefact, the command to run it with, what may be put on its standard input, the
policy that was COMPOSED for this run, and the limits. It is not told who the client is, it is not
given this gateway's signing key, it cannot read the ledger, and it has no way to ask about any run
but the one it was handed.

One thing does pass through it that is worth naming rather than leaving for somebody to find: the
one-time challenge, on the first line of the job's standard input. It is not a credential -- it
grants nothing, opens nothing and is destroyed when the run ends -- and it has to reach the process
inside the sandbox, which is on the worker's side of this line. See `gateway/challenge.py`.

## What this does not establish

Nothing here isolates anything. On one host the control plane and the code it sends still share a
kernel, and a vocabulary does not change that. What it establishes is that the product does not
know where the worker is, which is what makes moving it a matter of configuration.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

#: What the topology is, in the record, when the control plane and the worker are the same host.
#: Named here rather than described in prose somewhere, so that a report cannot fail to say it.
SINGLE_HOST_DEVELOPMENT = "single-host-development"

#: And what it is when they are not. Nothing produces this yet; it exists so that the value a
#: record carries is chosen from a list rather than written out at the place that happens to know.
SEPARATE_WORKER_HOST = "separate-worker-host"

TOPOLOGIES = (SINGLE_HOST_DEVELOPMENT, SEPARATE_WORKER_HOST)


class WorkerUnreachable(Exception):
    """The worker could not be asked. NOT that the job failed -- nobody knows whether it ran.

    `EM3C-EVIDENCE-0002` cost an external run to the difference between "the answer is no" and
    "there was no answer". A worker that cannot be reached is the second kind, and collapsing it
    into a failed job would tell a client something nobody established.
    """


class JobFailed(Exception):
    """The worker was reached, tried to run the job, and could not.

    Different from `WorkerUnreachable` in the one way that matters to a client: somebody DID try.
    What the worker managed to clean up before giving up travels on the exception, because a
    failure is not a reason to stop knowing whether a route out was left behind.
    """

    def __init__(self, message: str, egress_gone: bool | None = None) -> None:
        super().__init__(message)
        #: Whether the run's proxy and networks are gone, as far as the worker could tell.
        self.egress_gone = egress_gone


class NotData(TypeError):
    """Something was put in a message that could not be written down and read back.

    Raised where it is put there, not where it fails to cross. A value that cannot survive being
    sent is a value that would have made the worker's location matter.
    """


def _plain(value: Any, where: str) -> Any:
    """Whatever this is, in a form that could be written to a socket -- or a refusal.

    Recursive and strict: a message is checked as a whole when it is built, so that "this would
    not have crossed" is found on the machine that wrote it rather than on the one that could not
    read it.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        # Bytes cross as bytes; the transport decides how. What matters here is that they are
        # data rather than a handle to somebody's memory.
        return value
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise NotData(
                    where + " has a key that is not a name (" + type(key).__name__ + "), and a "
                    "message that cannot be written down is one that would have kept the worker "
                    "here")
        return {k: _plain(v, where + "." + k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v, where + "[]") for v in value]
    raise NotData(
        where + " is a " + type(value).__name__ + ", which cannot be written down and read back "
        "on another machine. Everything crossing this line is data: see agentnode_sdk.worker.")


@dataclass(frozen=True)
class Limits:
    """What a run may use. Every one of them is a number somebody else can enforce."""

    cpu: float = 1.0
    memory_mb: int = 512
    processes: int = 256
    wall_clock_s: int = 60
    #: How much the run may write. Zero means the worker's own default, which is what the
    #: container image gives; it is here because the alpha has to be able to say a number.
    storage_mb: int = 0


@dataclass(frozen=True)
class Job:
    """One unit of foreign code, and everything needed to run it somewhere else.

    There is nothing in here that means something only on this machine. `container_name` is a name
    the worker gives what it starts, not a path; `payload` is the bytes that go on standard input;
    `artifact` is the code itself.
    """

    run_id: str
    container_name: str
    command: tuple[str, ...]
    artifact: bytes
    #: What goes on the run's standard input, already assembled by the control plane. The
    #: challenge is its first line when there is one; see this module's docstring.
    stdin: str
    network: str = "none"
    allowed_domains: tuple[str, ...] = ()
    limits: Limits = field(default_factory=Limits)

    def as_message(self) -> dict[str, Any]:
        """This job, as something that could be sent. Refuses anything that could not cross."""
        body = asdict(self)
        body["command"] = list(self.command)
        body["allowed_domains"] = list(self.allowed_domains)
        return _plain(body, "job")


@dataclass(frozen=True)
class Outcome:
    """What happened to a job. Also only data.

    `reason` is why it stopped, in the protocol's words, and is empty while nothing has stopped.
    `exit_code` is a status the program CHOSE; something that was stopped did not choose one, and
    the runtime's own number is `native_status` with `native_platform` saying whose it is. That
    contract is `EM3C-E8-RECORD-0001` and it is the gateway's, not this module's -- what is here
    is the shape that carries it across.
    """

    exit_code: int | None
    stdout: str
    stderr: str
    reason: str = ""
    native_status: int | None = None
    native_platform: str = ""
    #: Whether what this run needed BESIDES its container is gone -- the egress proxy and its two
    #: networks, when it had them. None when there were none or when nobody could ask. The worker
    #: reports it because the handle to those things is a live object that cannot cross this line.
    egress_gone: bool | None = None
    #: Whose the worker's status numbers are, always. `native_platform` is what the runtime said
    #: about THIS stop and is empty for an ordinary exit; this one is the worker's own name for
    #: its numbers, so a run that was stopped can still say whose number it kept.
    runtime_platform: str = ""

    def as_message(self) -> dict[str, Any]:
        return _plain(asdict(self), "outcome")


@dataclass(frozen=True)
class Isolation:
    """What the worker can do about isolating code, as it reports it."""

    available: bool
    #: What runs the code there -- "docker", "podman", or "none".
    backend: str = "none"
    reason: str = ""
    #: The properties that were MEASURED there, not the ones claimed. The conformance report
    #: binds these, so they cross with everything else.
    measured: tuple[str, ...] = ()

    def as_message(self) -> dict[str, Any]:
        body = asdict(self)
        body["measured"] = list(self.measured)
        return _plain(body, "isolation")


@dataclass(frozen=True)
class Gone:
    """Whether what a run left behind is gone. Three answers, never two.

    `answered` is whether the runtime said anything at all. A listing from a command that FAILED
    is not an empty listing, and treating it as one reports a container gone because nobody could
    ask -- which is the hole EM-3B-R1 closed in the local backend and the same rule here.
    """

    answered: bool
    left: tuple[str, ...] = ()

    @property
    def verified(self) -> bool | None:
        """True when the runtime said nothing is left, False when something is, None when it
        could not be asked."""
        if not self.answered:
            return None
        return not self.left

    def as_message(self) -> dict[str, Any]:
        return _plain({"answered": self.answered, "left": list(self.left)}, "gone")


class Worker(ABC):
    """Whatever runs foreign code. It may be here, and it may not be.

    An implementation of this is the ONLY way the gateway reaches a container runtime. A gateway
    that called one directly would be a gateway that has to be where the runtime is.
    """

    #: Where this worker is, for the record to bind. One of `TOPOLOGIES`.
    topology = SINGLE_HOST_DEVELOPMENT

    #: What distinguishes one worker's configuration from another's, for the record to bind. A
    #: digest rather than the configuration itself: a record is read by people who may not be
    #: allowed to know what the worker was told.
    @abstractmethod
    def configuration_sha256(self) -> str:
        """A digest of the worker's own configuration, for conformance to bind."""

    @abstractmethod
    def can_it_isolate(self) -> Isolation:
        """What runs code there and whether it can isolate it."""

    @abstractmethod
    def runtime_version(self) -> str:
        """The runtime's own version, or "" when there is nothing to ask."""

    @abstractmethod
    def run(self, job: Job) -> Outcome:
        """Run this job and say what happened. Raises `WorkerUnreachable` if it could not be
        asked -- which is not the same as the job having failed."""

    @abstractmethod
    def stop(self, run_id: str, container_name: str, appear_seconds: float) -> bool:
        """Stop that run. True when something was removed."""

    @abstractmethod
    def gone(self, container_name: str, patiently: bool = True) -> Gone:
        """Whether what the run left behind is gone.

        `patiently` asks it to keep asking until the runtime says nothing is left or it runs out
        of time, because removal is not instantaneous. Without it the answer is one listing."""
