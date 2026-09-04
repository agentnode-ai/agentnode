"""EM-3B: what a conformance result is allowed to say.

The whole point of this module is that a property nobody measured cannot end up looking like a
property that holds. Three rules carry that, and they are enforced at construction rather than
documented:

* **Three assurance levels, and no fourth.** ``self-reported`` is what a backend or an argv
  asserts. ``observed`` is what this suite measured. ``attested`` needs an external attestation
  document and cannot be constructed without one. The word *certified* is rejected outright: no
  outside body has examined anything, and a stronger word would be a claim nobody made.
* **Evidence from the host cannot be ``observed``.** A Python object saying it refuses proves
  something about that object, not about the boundary. The boundary is the process, the container
  or the remote backend, so host-side and argv-shaped evidence is capped at ``self-reported`` --
  structurally, by refusing the combination.
* **A required property that was not measured blocks the whole report.** ``not_checked`` and
  ``probe_error`` are outcomes, not omissions, and a report carrying either for a required check
  is not conformant no matter how many other checks passed.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum

SUITE_VERSION = "em3b.1"

# Rejected everywhere, including inside evidence text. Nothing in this project may describe a
# result with a word that implies an outside body examined it.
FORBIDDEN = ("certified", "certification", "certifies", "certify")


class Assurance(str, Enum):
    SELF_REPORTED = "self-reported"
    OBSERVED = "observed"
    ATTESTED = "attested"


class Outcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_CHECKED = "not_checked"        # nothing measured it; the reason says why
    NOT_APPLICABLE = "not_applicable"  # the property cannot apply to this backend class
    PROBE_ERROR = "probe_error"        # the suite's own probe failed -- not a backend verdict


class Vantage(str, Enum):
    INSIDE = "inside"        # measured from within the payload, where untrusted code sits
    OUTSIDE = "outside"      # measured through the runtime's view of the live container
    BOTH = "both"
    HOST_SDK = "host-sdk"    # observed of the SDK process; never the boundary
    NONE = "none"            # nothing was measured


#: Outcomes that mean nothing was measured, so the assurance floor applies.
_UNMEASURED = (Outcome.NOT_CHECKED, Outcome.NOT_APPLICABLE, Outcome.PROBE_ERROR)
#: Vantages that cannot support an ``observed`` claim about the boundary.
_NOT_A_BOUNDARY = (Vantage.HOST_SDK, Vantage.NONE)


def _scan(*texts: object) -> None:
    for t in texts:
        s = t if isinstance(t, str) else json.dumps(t, default=str)
        low = s.lower()
        for word in FORBIDDEN:
            if word in low:
                raise ValueError(
                    f"a conformance result may not use the word {word!r}: the vocabulary is "
                    "self-reported, observed, attested, and nothing else"
                )


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    title: str
    family: str
    outcome: Outcome
    assurance: Assurance
    vantage: Vantage
    evidence: str
    required: bool = True
    detail: dict = field(default_factory=dict)
    attestation: str = ""

    def __post_init__(self) -> None:
        if not self.check_id or not self.title or not self.family:
            raise ValueError("a check result needs an id, a title and a family")
        if not self.evidence.strip():
            raise ValueError(f"{self.check_id}: every outcome needs its evidence or its reason")
        _scan(self.title, self.evidence, self.detail, self.attestation)
        if self.assurance is Assurance.ATTESTED and not self.attestation.strip():
            raise ValueError(
                f"{self.check_id}: 'attested' requires an external attestation document; "
                "no code path in this suite has one"
            )
        if self.assurance is Assurance.OBSERVED and self.vantage in _NOT_A_BOUNDARY:
            raise ValueError(
                f"{self.check_id}: {self.vantage.value} evidence cannot be 'observed'. The Python "
                "layer is not the boundary; only a measurement inside the payload or through the "
                "runtime's view of the live container is"
            )
        if self.outcome in _UNMEASURED and self.assurance is not Assurance.SELF_REPORTED:
            raise ValueError(
                f"{self.check_id}: outcome {self.outcome.value} means nothing was measured, so its "
                "assurance cannot be above self-reported"
            )

    # -- constructors: the assurance follows from HOW the result was reached ----------------

    @classmethod
    def measured(cls, check_id, title, family, ok, vantage, evidence, *,
                 required=True, detail=None):
        """A property this suite measured. The only route to 'observed'."""
        if vantage in _NOT_A_BOUNDARY:
            raise ValueError(f"{check_id}: {vantage.value} is not a measurement vantage")
        return cls(check_id, title, family, Outcome.PASS if ok else Outcome.FAIL,
                   Assurance.OBSERVED, vantage, evidence, required, dict(detail or {}))

    @classmethod
    def claimed(cls, check_id, title, family, ok, evidence, *,
                vantage=Vantage.HOST_SDK, required=True, detail=None):
        """Something the backend, its argv or the SDK asserts. Never rises above self-reported."""
        return cls(check_id, title, family, Outcome.PASS if ok else Outcome.FAIL,
                   Assurance.SELF_REPORTED, vantage, evidence, required, dict(detail or {}))

    @classmethod
    def not_checked(cls, check_id, title, family, reason, *, required=True):
        return cls(check_id, title, family, Outcome.NOT_CHECKED, Assurance.SELF_REPORTED,
                   Vantage.NONE, reason, required)

    @classmethod
    def not_applicable(cls, check_id, title, family, reason, *, required=True):
        return cls(check_id, title, family, Outcome.NOT_APPLICABLE, Assurance.SELF_REPORTED,
                   Vantage.NONE, reason, required)

    @classmethod
    def probe_error(cls, check_id, title, family, reason, *, required=True):
        """The suite's own probe failed. This is not a verdict about the backend."""
        return cls(check_id, title, family, Outcome.PROBE_ERROR, Assurance.SELF_REPORTED,
                   Vantage.NONE, reason, required)


@dataclass(frozen=True)
class ConformanceReport:
    backend_identity: str
    backend_version: str
    runtime: str
    image: str
    generated_at: str
    results: tuple
    is_test_double: bool = False
    suite_version: str = SUITE_VERSION

    def __post_init__(self) -> None:
        _scan(self.backend_identity, self.backend_version, self.runtime, self.image)
        seen = set()
        for r in self.results:
            if r.check_id in seen:
                raise ValueError(f"duplicate check id in one report: {r.check_id}")
            seen.add(r.check_id)

    # -- reading the report ------------------------------------------------------------------

    @property
    def required_results(self) -> tuple:
        return tuple(r for r in self.results if r.required)

    @property
    def unproven(self) -> tuple:
        """Required checks that do not carry the property: failures and non-measurements."""
        return tuple(r for r in self.required_results
                     if r.outcome in (Outcome.FAIL, Outcome.NOT_CHECKED, Outcome.PROBE_ERROR))

    @property
    def is_conformant(self) -> bool:
        """True only when every required property was measured or is genuinely inapplicable.

        A test double can never make this true: a double exists to test this suite, and its
        result is not evidence about any product backend.
        """
        return not self.is_test_double and not self.unproven

    def counts(self) -> dict:
        out = {o.value: 0 for o in Outcome}
        for r in self.results:
            out[r.outcome.value] += 1
        return out

    def summary_line(self) -> str:
        c = self.counts()
        n_obs = sum(1 for r in self.results if r.assurance is Assurance.OBSERVED)
        head = (f"{self.backend_identity} {self.backend_version}: {c['pass']} of "
                f"{len(self.required_results)} required properties hold, {n_obs} observed")
        if self.is_test_double:
            return head + " -- TEST DOUBLE: this is a test of the suite, not evidence about a backend"
        if self.unproven:
            return head + (f"; NOT conformant -- {len(self.unproven)} required "
                           f"{'property is' if len(self.unproven) == 1 else 'properties are'} "
                           "unproven: " + ", ".join(r.check_id for r in self.unproven))
        na = c["not_applicable"]
        tail = f" ({na} not applicable to this backend)" if na else ""
        return head + f"; conformant for the properties this suite covers{tail}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["results"] = [
            {**asdict(r), "outcome": r.outcome.value, "assurance": r.assurance.value,
             "vantage": r.vantage.value}
            for r in self.results
        ]
        d["is_conformant"] = self.is_conformant
        d["summary"] = self.summary_line()
        d["counts"] = self.counts()
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)
