"""EM-3B: run the conformance suite against the local container backend and gate on the result.

Evidence, not decoration. The report this writes is the only thing that entitles anyone to say a
property of the local sandbox was observed rather than intended, so the gate is deliberately blunt:

* a required property that FAILED is a defect in the backend -> non-zero;
* a required property that could not be measured (``not_checked``/``probe_error``) is a defect in
  the evidence -> also non-zero, because a report with holes in it must never be filed as proof;
* ``not_applicable`` is allowed, and named in the output, so it cannot pass unnoticed.

Run with a real container runtime present. It exits 0 only when the report says the properties hold
and says so on the strength of measurements.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

from agentnode_sdk.conformance import Outcome, SuiteOptions, run_conformance
from agentnode_sdk.conformance.runner import measure_egress
from agentnode_sdk.sandbox.container_backend import ContainerBackend

OUT = pathlib.Path(os.environ.get("CONFORMANCE_REPORT", "conformance-report.json"))


def main() -> int:
    backend = ContainerBackend()
    availability = backend.check_available()
    print(f"backend: {type(backend).__name__} available={availability.available} "
          f"runtime={availability.backend} reason={availability.reason!r}")
    if not availability.available:
        print("FAIL: this lane exists to measure a real container backend and none is available")
        return 2

    egress = None
    try:
        egress = measure_egress(backend)
        print("egress matrix: " + json.dumps(egress, sort_keys=True))
    except Exception as exc:                                        # noqa: BLE001 - reported
        print(f"the egress matrix could not be measured: {type(exc).__name__}: {exc}")

    report = run_conformance(
        backend,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        options=SuiteOptions(),
        egress_matrix=egress,
    )
    OUT.write_text(report.to_json(), encoding="utf-8")

    print()
    print(f"{'check':32s} {'outcome':13s} {'assurance':14s} vantage")
    print("-" * 100)
    for r in report.results:
        print(f"{r.check_id:32s} {r.outcome.value:13s} {r.assurance.value:14s} {r.vantage.value}")
        print(f"    {r.evidence[:150]}")
    print()
    print(report.summary_line())
    print(f"report written to {OUT}")

    bad = [r for r in report.results
           if r.required and r.outcome in (Outcome.FAIL, Outcome.NOT_CHECKED, Outcome.PROBE_ERROR)]
    if bad:
        print()
        print(f"FAIL: {len(bad)} required propert{'y is' if len(bad) == 1 else 'ies are'} unproven:")
        for r in bad:
            print(f"  {r.check_id} -> {r.outcome.value}: {r.evidence[:160]}")
        return 1
    na = [r.check_id for r in report.results if r.outcome is Outcome.NOT_APPLICABLE]
    if na:
        print(f"not applicable to this backend, and recorded as such: {', '.join(na)}")
    print("PASS: every required property was measured and holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
