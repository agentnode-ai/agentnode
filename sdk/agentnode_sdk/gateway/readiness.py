"""Whether this gateway may take work, decided from measurements rather than from its own say-so.

The gateway used to answer that question like this::

    "container_isolation": bool(availability.available),
    "verified_cleanup":    bool(availability.available),

A container runtime being installed was reported as proof that the runtime isolates, and as proof
that cleanup is verified. Neither follows. A client asking for `verified_cleanup` was checked
against that claim and told yes, which is the recurring defect in this codebase wearing new
clothes: a check that could not see its input reporting the good answer.

What replaces it is a stored conformance report -- the same suite that runs in CI, with its own
notion of `measured` versus `self-reported` -- and four questions asked of it before any of its
contents are believed:

1. **Is there one at all?** No report is not a passing report.
2. **Is it about THIS gateway?** Bound to gateway id, gateway version, backend and runtime
   version. A report copied from another machine describes that machine.
3. **Is it recent?** A measurement from before the last upgrade describes software that is no
   longer running.
4. **Did every required property actually get measured?** `not_checked` and `probe_error` are
   outcomes, not omissions. A property nobody could measure is not a property that holds.

Only then is a property true. Anything else -- missing, stale, foreign, unmeasured, failed -- is
false, and a false required property means the gateway is not ready and runs nothing.

## Unknown is not success

`verified_cleanup` deserves its own note, because it is where "unknown" is most tempting to round
up. If cleanup cannot be verified, the honest answer is that it is unknown, and a gateway claiming
complete cleanup as a required property while being unable to measure it is not conformant. The
run-level rule is the same: a job that required verified cleanup and finished with cleanup unknown
did not satisfy what it asked for, and saying otherwise would make the requirement decorative.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: How long a measurement describes the thing it measured. A day is not a security boundary; it is
#: an assumption about how often a machine changes underneath its own report.
MAX_REPORT_AGE_SECONDS = 24 * 60 * 60

#: Gateway property -> the conformance checks that must have been MEASURED and passed for it.
#: A mapping that named a check the suite does not produce would silently make its property
#: unreachable, which reads as "not ready" and would eventually be explained away rather than
#: fixed -- so a test runs the suite and compares these ids against the ones it really emits.
PROPERTY_CHECKS: dict[str, tuple[str, ...]] = {
    "container_isolation": ("outside-host-process", "not-root"),
    "network_none": ("network-mode",),
    "memory_ceiling_enforceable": ("limit-memory",),
    "verified_cleanup": ("run-leaves-nothing", "cancel-and-kill"),
    "egress_allowlist": ("egress-allowlist",),
}

#: The two words a stored result must carry to count. Taken from the report's own vocabulary:
#: `observed` is what separates a measurement from something the SDK merely says about itself, and
#: `pass` is the only outcome that is a yes -- `not_checked`, `probe_error` and `not_applicable`
#: are all outcomes rather than omissions, and none of them is proof.
OBSERVED = "observed"
PASSED = "pass"

#: Properties this gateway will not run anything without, whatever a job asks for.
ALWAYS_REQUIRED: tuple[str, ...] = ("container_isolation",)


@dataclass(frozen=True)
class ReportBinding:
    """What a report is about. A report that is not about this gateway is somebody else's."""

    gateway_id: str = ""
    gateway_version: str = ""
    backend: str = ""
    #: The image the measurement was taken against. Binding to this rather than to a runtime
    #: version string is deliberate: the image is what the job actually runs inside, so a report
    #: taken against a different image describes different software even on the same daemon.
    image_digest: str = ""

    #: Which boot of this machine the measurement happened during. A reboot can bring a new
    #: kernel, a cgroup controller that is no longer mounted, or a seccomp or apparmor policy that
    #: loaded differently -- none of which move the image digest, and all of which change what the
    #: container actually gets. Without this the report would describe the previous boot and still
    #: look current.
    boot_id: str = ""

    #: The version of the runtime the measurement ran against, distinct from which runtime it is.
    backend_version: str = ""

    #: The version of the conformance schema the report speaks. A report whose vocabulary has
    #: changed cannot be read against today's expectations and called current.
    conformance_schema: str = ""

    #: The digest of the operator policy this measurement was taken FOR. `EM3C-EXTERNAL-0017`
    #: found readiness saying nothing about the policy underneath it: a report taken while the
    #: gateway allowed no network was accepted as evidence after the operator opened an
    #: allowlist. Two enforcement modes, one measurement, and nothing that noticed. A report is
    #: now about a policy, and stops being evidence the moment that policy changes.
    operator_policy_digest: str = ""

    #: Where the code this report is about actually runs, relative to the gateway that signed it.
    #: `ALPHA-BOUNDARY-0001` decided foreign code belongs on another machine; until it moves, a
    #: report that did not say which arrangement it was measured under would be read as describing
    #: the other one. One of `agentnode_sdk.worker.TOPOLOGIES`.
    worker_topology: str = ""

    #: What the worker was configured as when the measurement was taken. A digest rather than the
    #: configuration: a report is read by people who are not necessarily allowed to know what the
    #: worker was told, and a measurement taken against a differently configured worker describes
    #: something else even on the same machine.
    worker_configuration_sha256: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "gateway_id": self.gateway_id,
            "gateway_version": self.gateway_version,
            "backend": self.backend,
            "image_digest": self.image_digest,
            "boot_id": self.boot_id,
            "backend_version": self.backend_version,
            "conformance_schema": self.conformance_schema,
            "operator_policy_digest": self.operator_policy_digest,
            "worker_topology": self.worker_topology,
            "worker_configuration_sha256": self.worker_configuration_sha256,
        }

    def mismatches(self, other: "ReportBinding") -> tuple[str, ...]:
        return tuple(
            field_name for field_name, mine in self.as_dict().items()
            if str(other.as_dict().get(field_name, "")) != str(mine)
        )


@dataclass(frozen=True)
class Readiness:
    """The answer, with the reason attached. Never a bare boolean."""

    ready: bool
    reason: str = ""
    properties: dict[str, bool] = field(default_factory=dict)
    unproven: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    measured_at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "reason": self.reason,
            "properties": dict(self.properties),
            "unproven": list(self.unproven),
            "next_steps": list(self.next_steps),
            "measured_at": self.measured_at,
        }


_MEASURE_STEP = "agentnode gateway doctor --measure"


class ReadinessGate:
    """Holds this gateway's conformance report and decides what it proves."""

    def __init__(self, root: str | os.PathLike[str],
                 max_age_seconds: float = MAX_REPORT_AGE_SECONDS) -> None:
        self.path = Path(root) / "conformance.json"
        self.max_age_seconds = max_age_seconds

    # ------------------------------------------------------------------ storage

    def store(self, report: dict, binding: ReportBinding, now: float | None = None) -> None:
        """Record a measurement, stamped with what it is about and when it was taken."""
        now = time.time() if now is None else now
        document = {
            "measured_at": now,
            "binding": binding.as_dict(),
            "report": report,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".conformance-")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(document, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def load(self) -> dict | None:
        if not self.path.is_file():
            return None
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Unreadable is not passing. It is reported as no measurement at all.
            return None
        return loaded if isinstance(loaded, dict) else None

    # ------------------------------------------------------------------ the decision

    def evaluate(self, binding: ReportBinding, required: tuple[str, ...] | None = None,
                 now: float | None = None) -> Readiness:
        """Whether the stored measurement proves what THIS policy needs proved.

        `required` is the set the active operator policy demands, which is why it is a parameter
        rather than a constant: an allowlist policy and a closed one need different things
        measured, and a gateway that asked the same question of both would accept a report taken
        under one as evidence for the other -- `EM3C-Y6-DECISION-0001`, `D2`.

        A required property this build has no check for is unproven, not waived. That is what
        keeps a mode nobody has implemented a measurement for -- `network_unrestricted` today --
        from being ready by omission.
        """
        return self.evaluate_document(self.load(), binding, required, now)

    def evaluate_document(self, document, binding: ReportBinding,
                          required: tuple[str, ...] | None = None,
                          now: float | None = None) -> Readiness:
        """The same judgement, on a document the caller supplies.

        Split out because the report that decides readiness lives inside the authenticated
        active-state snapshot (`EM3C-Y6-DECISION-0001`, `D4-a`), while this file on its own is
        kept as a diagnostic copy. The judging is identical either way; only where the document
        came from differs, and that is the caller's business.
        """
        now = time.time() if now is None else now
        required = tuple(ALWAYS_REQUIRED) if required is None else tuple(required)
        blank = {name: False for name in sorted(set(PROPERTY_CHECKS) | set(required))}

        if document is None:
            return Readiness(
                False,
                "this gateway has not been measured yet, so it cannot say what it enforces. "
                "Nothing will be run until it has been.",
                blank, tuple(sorted(PROPERTY_CHECKS)), (_MEASURE_STEP,),
            )

        stored = ReportBinding(**{
            k: str(v) for k, v in (document.get("binding") or {}).items()
            if k in ReportBinding.__dataclass_fields__
        })
        drift = binding.mismatches(stored)
        if drift:
            rebooted = drift == ("boot_id",)
            # Only when it is the ONLY thing that moved. A report that is also from another
            # machine is not explained by a policy change, and saying so would describe the
            # smaller problem and hide the larger one.
            policy_changed = drift == ("operator_policy_digest",)
            if policy_changed:
                # The most likely reason to be here, and the one worth its own sentence: the
                # measurement is fine, it is just not a measurement of what is now configured.
                return Readiness(
                    False,
                    "what this gateway allows has changed since it was last measured, so the "
                    "measurement describes a different policy. Nothing runs until the policy in "
                    "force has been measured as itself.",
                    blank, tuple(sorted(set(PROPERTY_CHECKS) | set(required))), (_MEASURE_STEP,),
                    measured_at=document.get("measured_at"),
                )
            return Readiness(
                False,
                ("this machine has restarted since it was last measured, and a restart can change "
                 "what a container actually gets -- a new kernel, a cgroup controller that is no "
                 "longer mounted, a policy that loaded differently. The old measurement is not "
                 "wrong, it just describes the previous boot.")
                if rebooted else
                ("the stored measurement describes something else (" + ", ".join(drift) +
                 " differ), so it says nothing about what is running here."),
                blank, tuple(sorted(PROPERTY_CHECKS)), (_MEASURE_STEP,),
                measured_at=document.get("measured_at"),
            )

        measured_at = float(document.get("measured_at") or 0.0)
        age = now - measured_at
        if age > self.max_age_seconds or age < -abs(self.max_age_seconds):
            hours = max(1, int(age // 3600))
            return Readiness(
                False,
                f"the last measurement is about {hours} hours old, which is too old to describe "
                "what is running now.",
                blank, tuple(sorted(PROPERTY_CHECKS)), (_MEASURE_STEP,),
                measured_at=measured_at,
            )

        results = {
            str(r.get("check_id")): r
            for r in ((document.get("report") or {}).get("results") or [])
            if isinstance(r, dict)
        }
        # A serialised CheckResult carries `outcome` and `assurance`. It has no boolean `ok` --
        # `ok` is a constructor argument that becomes an outcome. Reading `ok` here meant reading
        # a key that is never present, so every property came out unproven no matter what the
        # suite had found: a check that could not see its input, failing closed but still blind.
        properties: dict[str, bool] = {}
        unproven: list[str] = []
        for name in sorted(set(PROPERTY_CHECKS) | set(required)):
            check_ids = PROPERTY_CHECKS.get(name)
            if not check_ids:
                # Required, and nothing in this build measures it. Unproven, and therefore false.
                properties[name] = False
                unproven.append(name)
                continue
            holds = True
            for check_id in check_ids:
                result = results.get(check_id)
                # Missing, not measured, or measured false -- all three are "not proven".
                # `not_checked` and `probe_error` are outcomes, not omissions.
                if (result is None
                        or str(result.get("assurance")) != OBSERVED
                        or str(result.get("outcome")) != PASSED):
                    holds = False
                    break
            properties[name] = holds
            if not holds:
                unproven.append(name)

        missing_core = [p for p in required if not properties.get(p)]
        if missing_core:
            return Readiness(
                False,
                "this gateway cannot show that it " + _plain(missing_core[0]) +
                ", so it will not run anything.",
                properties, tuple(sorted(unproven)), (_MEASURE_STEP,),
                measured_at=measured_at,
            )

        return Readiness(True, "", properties, tuple(sorted(unproven)), (), measured_at)


def _plain(property_name: str) -> str:
    """Property names are for the wire. People get a sentence."""
    return {
        "container_isolation": "runs code in a container separate from the host",
        "network_none": "can shut a job off the network",
        "memory_ceiling_enforceable": "can hold a job to a memory ceiling",
        "verified_cleanup": "can show that a finished job left nothing behind",
        "egress_allowlist": "can hold a job to an allowed list of destinations",
    }.get(property_name, property_name.replace("_", " "))


def describe_missing(names) -> str:
    """A sentence a person can act on, for one or more unproven properties."""
    parts = [_plain(n) for n in names]
    if not parts:
        return ""
    if len(parts) == 1:
        return "this gateway cannot show that it " + parts[0]
    return "this gateway cannot show that it " + ", ".join(parts[:-1]) + ", or " + parts[-1]
