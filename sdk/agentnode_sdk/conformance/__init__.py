"""EM-3B: the backend conformance suite.

One question, asked the same way of every sandbox backend: **do the properties it claims actually
hold?** The local container backend that exists today, a sandbox an operator runs themselves, and a
managed one all have to answer it, and none of them can be trusted on description alone -- this
codebase has already carried module docstrings that described a capability as inert while two run
paths were calling it.

So the suite measures. It runs a probe inside the payload, where untrusted code actually sits, and
asks the runtime what it sees from outside where that is possible. Evidence that consists of an argv
or of a Python object's assertion is capped at ``self-reported`` and cannot be promoted; a property
nobody measured is ``not_checked``; and a required property that was not measured keeps the whole
report from being conformant.

Three assurance levels, and there is no fourth: ``self-reported``, ``observed``, ``attested``.
``attested`` needs an external attestation document and no code path here has one. The word
*certified* is rejected at construction, because no outside body has examined anything.

    from agentnode_sdk.conformance import run_conformance
    from agentnode_sdk.sandbox.policy import get_default_backend

    report = run_conformance(get_default_backend(), generated_at="2026-09-03T00:00:00Z")
    print(report.summary_line())
"""
from agentnode_sdk.conformance.checks import Context, run_all
from agentnode_sdk.conformance.report import (
    SUITE_VERSION,
    Assurance,
    CheckResult,
    ConformanceReport,
    Outcome,
    Vantage,
)
from agentnode_sdk.conformance.runner import SuiteOptions, run_conformance

__all__ = [
    "Assurance",
    "CheckResult",
    "ConformanceReport",
    "Context",
    "Outcome",
    "SUITE_VERSION",
    "SuiteOptions",
    "Vantage",
    "run_all",
    "run_conformance",
]
