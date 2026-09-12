"""The only thing the control plane may ask of whatever runs foreign code.

`ALPHA-BOUNDARY-0001` decided that foreign code belongs on a different machine from the process
that holds every client's token material and this gateway's signing identity. That machine is not
being bought yet; the alpha is built on one host as a closed development environment. What has to
be true NOW is that no BEHAVIOUR of the product depends on the worker being here, so that the move
is a transport away rather than a rewrite.

Read that precisely, because a looser reading of it was wrong and a review caught it. This build
speaks unix sockets and nothing else: `from_address` refuses every other scheme in as many words.
So a worker on another machine is NOT something this build can be configured into -- it needs a
transport that does not exist yet, and adding one is a change to the product, not to a
deployment. What IS established is narrower and is the part that was expensive: the vocabulary,
the data and the failure modes do not depend on co-location, so the transport is the only thing
missing rather than one of many.

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
kernel, and a vocabulary does not change that.

Nor does it establish that the worker can be moved. `SocketWorker` reaches a unix socket, which
is on this machine by construction, and there is no transport here that crosses a network. The
move needs one to be written. Nothing in this build has been exercised against a worker on
another host, and no claim here should be read as saying otherwise: what the seam establishes is
that such a transport would be the only thing to add, not that adding it is configuration.
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

#: What each topology does NOT protect against, in the record itself.
#:
#: A label is only meaningful to somebody who already knows what it means. A person reading a
#: record months later, or somebody handed one as evidence, meets the word
#: "single-host-development" and has nothing to tell them what it implies -- and the implication
#: is the whole point of recording it. So the limits travel with the label rather than living
#: only in a file next to the code.
WHAT_A_TOPOLOGY_DOES_NOT_ESTABLISH = {
    SINGLE_HOST_DEVELOPMENT: (
        "The control plane and the sandbox worker are separate accounts on ONE kernel. That is "
        "not isolation between them and not a tenancy boundary: an escape from the sandbox "
        "reaches the host that holds this gateway's signing identity and every client's token. "
        "This is a development and closed-alpha arrangement. It is not production-ready, not "
        "multi-tenant, and not escape-proof between control plane and worker."),
    SEPARATE_WORKER_HOST: (
        "The worker is on its own machine. What that establishes has not been measured, because "
        "nothing has run in that arrangement yet; this text exists so a record made under it is "
        "not read as carrying a claim nobody checked."),
}


def what_it_does_not_establish(topology: str) -> str:
    """The limits of the arrangement a record was made under, for the record to carry."""
    return WHAT_A_TOPOLOGY_DOES_NOT_ESTABLISH.get(
        str(topology),
        "This record was made under an arrangement this build does not describe, so nothing "
        "about what it does or does not protect against can be read from it.")


class WorkerUnreachable(Exception):
    """The worker could not be asked. NOT that the job failed -- nobody knows whether it ran.

    `EM3C-EVIDENCE-0002` cost an external run to the difference between "the answer is no" and
    "there was no answer". A worker that cannot be reached is the second kind, and collapsing it
    into a failed job would tell a client something nobody established.
    """


class NoRuntimeThere(Exception):
    """The worker was reached, answered, and has nothing that can isolate anything.

    Its own kind, because it is not the other two and the difference decides what somebody does
    next. `WorkerUnreachable` means nobody answered and nothing is known -- look at the network,
    the socket, whether the service is up. This means the worker answered and told us: its host
    has no usable container runtime, and no amount of retrying will change that. `JobFailed`
    means it ran and did not work, which is about the job.

    Collapsing this into "unreachable" sent people to look at a connection that was working
    perfectly, which is the kind of wrong answer that costs an afternoon.
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


class CouldNotRestrictTheNetwork(JobFailed):
    """The job asked for a restricted network and the worker could not build one.

    Its own kind, because the alternative is running it on a bare network -- which is the failure
    the whole restricted path exists to prevent -- and because a client that asked for one door
    needs to be told there was no door rather than that something went wrong.
    """


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


@dataclass(frozen=True)
class Ceilings:
    """Whether this worker's limits were shown to BIND, here, as it is configured.

    Kept apart from `Isolation` on purpose, because they answer different questions and the
    first has been mistaken for the second. `Isolation` asks whether a runtime is present and
    whether this host could in principle hold a ceiling -- for podman that is read off the
    cgroup version. This asks whether an allocation past the ceiling was actually STOPPED, on
    this machine, by this account, under the unit this worker is running in.

    The distance between those two is not theoretical. A rootless runtime on a cgroup-v2 host
    reports that it can hold a ceiling, accepts the flag, and then silently does not apply it
    when it has no systemd cgroup manager to delegate through: the allocation walks past the
    limit and finishes normally. Every claim in the first question is true in that state, and a
    job running under it has no memory limit at all.
    """

    #: True when an allocation past the ceiling was stopped BY the ceiling. False when it ran
    #: straight through. None when the runtime is somewhere else and this object is not the one
    #: that can answer -- never as a stand-in for "probably fine".
    held: bool | None
    #: Why, in words an operator can act on.
    reason: str = ""
    #: What was measured, so a refusal can be read rather than believed.
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_message(self) -> dict[str, Any]:
        return _plain(asdict(self), "ceilings")


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
    def instance_label(self) -> str:
        """What to call whatever runs jobs here, in a run's binding. Not an address and not a
        secret: a name for the thing, so that two runs can be seen to have been executed by the
        same one or by different ones."""

    @abstractmethod
    def image_digest(self) -> str:
        """The image a job runs inside, as the worker knows it. Empty when there is none."""

    @abstractmethod
    def can_it_isolate(self) -> Isolation:
        """What runs code there and whether it can isolate it."""

    @abstractmethod
    def prove_its_ceilings(self, *, megabytes: int = 0, run_id: str = "") -> Ceilings:
        """Show that a ceiling BINDS here, by hitting it.

        A measurement, not a lookup -- because the lookup is the part that has been wrong.
        A worker answers this before it agrees to run anybody's code.
        """

    @abstractmethod
    def runtime_version(self) -> str:
        """The runtime's own version, or "" when there is nothing to ask."""

    @abstractmethod
    def measure(self, *, generated_at, options, egress_matrix, egress_expected):
        """Measure what this worker really enforces, and return the report AS A DOCUMENT.

        The measurement belongs where the runtime is. A control plane that measured a worker it
        could not reach would be reporting on something it had not touched -- and a report that
        came back as an object with methods on it would be a report that could not have come from
        another machine."""

    @abstractmethod
    def measure_egress(self, *, allowed, denied):
        """Try every destination the policy permits and every one it does not, there."""

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
