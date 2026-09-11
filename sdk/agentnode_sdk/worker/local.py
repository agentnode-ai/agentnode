"""A worker on this machine. The same vocabulary something on another machine would answer.

Everything here used to be inside `GatewayService`: building the container specification, setting
up and tearing down the egress proxy, shelling out to the runtime to list and remove containers,
and asking it for its version. None of it was wrong there -- it was simply on the wrong side of a
line that did not exist yet.

It is here now so that the control plane holds a `Worker` and never a runtime, and so that the
implementation that speaks over a socket can be written without the gateway noticing. What the
gateway does with an `Outcome` is unchanged; where the `Outcome` comes from is the whole point.

## What this does not establish

This worker is in the same process as the control plane, on the same machine, under the same user.
It isolates the JOB -- that is what the container is for, and it is measured -- but it does not
isolate the control plane from the job's host, and nothing in this file pretends to.
`ALPHA-BOUNDARY-0001` settled where foreign code belongs; this is the provisional arrangement
until the worker moves, and it reports itself as `single-host-development` so that no record can
quietly suggest otherwise.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time

from agentnode_sdk.worker import (
    SINGLE_HOST_DEVELOPMENT,
    CouldNotRestrictTheNetwork,
    Gone,
    Isolation,
    Job,
    JobFailed,
    Outcome,
    Worker,
)

#: How long to keep asking a runtime whether what a run left behind is gone. Removal is not
#: instantaneous and sampling once can catch a container mid-teardown.
GONE_SECONDS = 30.0


class LocalWorker(Worker):
    """Runs jobs through a sandbox backend in this process."""

    topology = SINGLE_HOST_DEVELOPMENT

    def __init__(self, backend) -> None:
        #: What actually isolates a job. Held here and nowhere else: a gateway that could reach
        #: it would be a gateway that has to be where it is.
        self.backend = backend
        self._version_cache: str | None = None

    # ------------------------------------------------------------------ what it is

    def configuration_sha256(self) -> str:
        """A digest of what distinguishes this worker from another one.

        Not the configuration itself: a conformance report is read by people who are not
        necessarily allowed to know what the worker was told, and a digest binds it without
        disclosing it.
        """
        isolation = self.can_it_isolate()
        described = {
            "topology": self.topology,
            "kind": type(self).__name__,
            "backend": isolation.backend,
            "backend_version": self.runtime_version(),
            "image_digest": self.image_digest(),
        }
        return hashlib.sha256(
            json.dumps(described, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def instance_label(self) -> str:
        return type(self.backend).__name__

    def image_digest(self) -> str:
        return str(getattr(self.backend.check_available(), "image_digest", "") or "")

    def can_it_isolate(self) -> Isolation:
        availability = self.backend.check_available()
        return Isolation(
            available=bool(getattr(availability, "available", False)),
            backend=str(getattr(availability, "backend", "") or "none"),
            reason=str(getattr(availability, "reason", "") or ""),
            measured=tuple(getattr(availability, "measured", ()) or ()),
        )

    def runtime_version(self) -> str:
        """The runtime's own version, asked once per process.

        The same lifetime this has always had: asking a runtime for its version is a subprocess,
        and doing it per job would be a real cost for a value that changes when a daemon is
        upgraded -- which restarts the daemon and, in practice, everything above it. A runtime
        upgraded underneath a running worker is caught at its next start or measurement. That is a
        stated limit, not an assumption that it cannot happen.
        """
        if self._version_cache is not None:
            return self._version_cache
        from agentnode_sdk.conformance.runner import _runtime_version

        runtime = str(self.backend.check_available().backend or "")
        value = ""
        if runtime and runtime != "none":
            try:
                value = str(_runtime_version(runtime) or "")
            except Exception:                                 # noqa: BLE001
                value = ""
        self._version_cache = value
        return value

    # ------------------------------------------------------------------ measuring it

    def measure(self, *, generated_at, options, egress_matrix, egress_expected):
        from agentnode_sdk.conformance.runner import run_conformance

        return run_conformance(self.backend, generated_at=generated_at, options=options,
                               egress_matrix=egress_matrix, egress_expected=egress_expected)

    def measure_egress(self, *, allowed, denied):
        from agentnode_sdk.conformance.runner import measure_egress

        return measure_egress(self.backend, allowed=allowed, denied=denied)

    # ------------------------------------------------------------------ running one

    def run(self, job: Job) -> Outcome:
        """Run it, and clean up what running it needed.

        The egress proxy and its two networks belong to this run and to this side of the line:
        the handle is a live object that could not cross one, which is precisely why setting it
        up and tearing it down is the worker's and not the gateway's.
        """
        from agentnode_sdk.sandbox.types import ProcessSpec

        egress = None
        if job.network == "egress":
            # The same mechanism the local runners use: an --internal network with no route out,
            # plus a dual-homed CONNECT proxy that is the only way through. The job does not get a
            # filtered internet -- it gets no route at all, and one door.
            from agentnode_sdk.sandbox.egress import start_egress_proxy

            try:
                egress = start_egress_proxy(list(job.allowed_domains))
            except Exception as exc:                          # noqa: BLE001
                # Nothing has started, so there is nothing to clean up and nothing to report
                # about a route out that was never opened.
                raise CouldNotRestrictTheNetwork(str(exc)) from exc

        spec = ProcessSpec(
            command=list(job.command),
            network=job.network,
            egress=egress.spec if egress is not None else None,
            clean_home=True,
            interactive=True,
            name=job.container_name,
        )
        trouble = None
        result = None
        try:
            result = self.backend.run_process(
                spec, input_text=job.stdin, timeout=float(job.limits.wall_clock_s)
            )
        except Exception as exc:                              # noqa: BLE001
            trouble = exc
        finally:
            if egress is not None:
                from agentnode_sdk.sandbox.egress import stop_egress_proxy

                try:
                    stop_egress_proxy(egress)
                except Exception:                             # noqa: BLE001
                    pass
        left = self._egress_gone(egress) if egress is not None else None
        if trouble is not None:
            # A job that could not be run is still a run whose route out has to be accounted for,
            # so what was established about that travels with the failure rather than being lost
            # because something else went wrong.
            raise JobFailed(str(trouble), egress_gone=left) from trouble

        from agentnode_sdk.sandbox.backend import why_it_stopped

        code, out, err = result
        reason, native, platform = why_it_stopped(result)
        return Outcome(
            exit_code=code,
            stdout=out or "",
            stderr=err or "",
            reason=str(reason or ""),
            native_status=native,
            native_platform=str(platform or ""),
            egress_gone=left,
            runtime_platform=str(getattr(self.backend, "native_platform", "") or ""),
        )

    # ------------------------------------------------------------------ stopping one

    def stop(self, run_id: str, container_name: str, appear_seconds: float) -> bool:
        """Remove this run's container by the identity the backend actually gave it.

        Nothing outside this run's own prefix is ever addressed, and a listing that failed stops
        the removal rather than making it guess at a name.

        A cancel can arrive before the container exists: a run is marked running and the runtime
        then takes a moment to create it. Removing nothing at that instant and returning would
        leave the payload to run to its wall clock, so this waits briefly for one to appear --
        bounded, because a container that never appears is a run that never started.
        """
        runtime = str(self.backend.check_available().backend or "")
        if not runtime or runtime == "none" or not container_name:
            return False
        deadline = time.monotonic() + float(appear_seconds)
        names: list[str] = []
        while time.monotonic() < deadline:
            answer = self.gone(container_name, patiently=False)
            names = list(answer.left)
            if answer.answered and names:
                break
            time.sleep(0.25)
        removed = False
        for name in names:
            try:
                subprocess.run([runtime, "rm", "-f", name], capture_output=True, timeout=60)
                removed = True
            except Exception:                                 # noqa: BLE001
                pass
        return removed

    # ------------------------------------------------------------------ what is left

    def gone(self, container_name: str, patiently: bool = True) -> Gone:
        """Whether what this run left behind is gone -- yes, no, or nobody could say.

        The prefix, not the exact name: the backend gives every run its own generated identity
        (`<name>-<suffix>`), so the name the control plane chose is a PREFIX of what really ran.
        """
        if not patiently:
            return self._named(container_name)
        # Retrying is only worth anything against a runtime that ANSWERS. If there is nothing to
        # ask, thirty seconds of asking again produces the same "could not say" it produced at
        # once, and every run pays for it. Distinguish the two before looping: unaskable now is
        # unaskable later, while "still there" is exactly what changes with time.
        runtime = str(self.backend.check_available().backend or "")
        if not runtime or runtime == "none":
            return Gone(answered=False)
        deadline = time.monotonic() + GONE_SECONDS
        answer = Gone(answered=False, left=("pending",))
        while time.monotonic() < deadline:
            answer = self._named(container_name)
            if answer.answered and not answer.left:
                return answer
            time.sleep(0.25)
        return answer

    def _named(self, prefix: str) -> Gone:
        """One listing. `answered` is whether the runtime said anything at all.

        An empty listing from a command that FAILED is not an empty listing of containers, and
        treating it as one reports a container gone because nobody could ask. EM-3B-R1 closed
        exactly that hole in the local backend; the same rule holds here.
        """
        asker = getattr(self.backend, "containers_named", None)
        if asker is not None:
            # A backend that ran the container is best placed to say whether it is gone. This
            # also keeps the question honest under test: a stand-in that never started a
            # container must not be interrogated by asking a REAL runtime about a name it has
            # never created.
            answered, names = asker(prefix)
            return Gone(answered=bool(answered), left=tuple(names))
        runtime = str(self.backend.check_available().backend or "")
        if not runtime or runtime == "none" or not prefix:
            return Gone(answered=False)
        try:
            listed = subprocess.run(
                [runtime, "ps", "-a", "--filter", f"name={prefix}", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=30,
            )
        except Exception:                                     # noqa: BLE001
            return Gone(answered=False)
        if listed.returncode != 0:
            return Gone(answered=False)
        return Gone(answered=True,
                    left=tuple(n for n in listed.stdout.split() if n.startswith(prefix)))

    def _egress_gone(self, handle) -> bool | None:
        """Whether this run's proxy and its two networks are gone. None when unaskable.

        Separate from the container check because they are separate objects: a container can be
        removed while the network it sat on stays, and a leftover network with a proxy on it is a
        route out that nothing is using and nobody is watching.
        """
        runtime = str(self.backend.check_available().backend or "")
        if not runtime or runtime == "none":
            return None
        # A container is listed with .Names and a network with .Name. Asking for the wrong one
        # makes the runtime fail the template rather than answer, which comes back as "could not
        # ask" -- unknown rather than a false yes, but still blind.
        for kind, field, name in (("container", "{{.Names}}", handle.proxy_name),
                                  ("network", "{{.Name}}", handle.int_net),
                                  ("network", "{{.Name}}", handle.ext_net)):
            try:
                listed = subprocess.run(
                    [runtime, kind, "ls", "--filter", f"name={name}", "--format", field],
                    capture_output=True, text=True, timeout=30,
                )
            except Exception:                                 # noqa: BLE001
                return None
            if listed.returncode != 0:
                return None                                   # could not ask is not "gone"
            if any(line.strip() == name for line in listed.stdout.splitlines()):
                return False
        return True
