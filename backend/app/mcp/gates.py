"""Slice 2a — MCP auto-publish gate evaluator (advisory only, activates nothing).

A PURE function that turns the server-derived facts + the advisory client report
into a structured, evidenced ``GateResult``. It makes visible which submissions
would be auto-publish-eligible, which gates block, which are advisory, and why a
submission stays in review — WITHOUT changing any status, without a migration,
and without auto-publishing anything.

Safety invariant: two gates require infrastructure that does not exist yet
(automatable ownership proof, server-authoritative sandbox smoke). They are
``blocking`` and can never pass today, so ``auto_publish_eligible`` is ALWAYS
False. ``objective_blockers`` reports the blockers EXCLUDING those future gates —
so a submission whose only remaining blockers are the future gates reads as
"objectively clean, pending infrastructure".

No I/O here: the caller supplies ``typosquat_hit`` (and optionally the ownership
claim) so this stays a pure, table-testable function.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Ownership methods that count as an AUTOMATED, unforgeable proof. Empty today:
# the only fulfillment path is ``manual_admin`` (an admin attests), which is the
# review step this arc exists to remove — so it is NOT an auto proof. Slice 2b
# adds npm provenance / PyPI Trusted Publishing / verified maintainer here.
AUTOMATED_OWNERSHIP_METHODS: frozenset[str] = frozenset()


def _gate(id, label, passed, blocking, reason="", evidence=None, future=False):
    return {
        "id": id,
        "label": label,
        "passed": bool(passed),
        "blocking": bool(blocking),
        "future": bool(future),
        "reason": reason,
        "evidence": evidence or {},
    }


def evaluate_gates(
    *,
    manifest: dict,
    server_verification: dict,
    report: dict | None = None,
    typosquat_hit: bool = False,
    ownership_method: str | None = None,
) -> dict:
    """Evaluate the hard + advisory gates for a submission. Pure; returns a dict
    ready to store in the ``server_verification`` JSONB (no migration)."""
    report = report or {}
    sv = server_verification or {}
    mcp_server = manifest.get("mcp_server")

    sstatus = sv.get("server_status")
    actions = report.get("actions") or []
    high = [a for a in actions if a.get("severity") == "high"]
    credentialed = bool((mcp_server or {}).get("env_keys"))

    gates: list[dict] = [
        _gate(
            "runtime_mcp",
            "Manifest runtime is mcp",
            manifest.get("runtime") == "mcp",
            True,
            evidence={"runtime": manifest.get("runtime")},
        ),
        _gate(
            "mcp_server_present",
            "mcp_server block present",
            isinstance(mcp_server, dict),
            True,
        ),
        _gate(
            "package_exists",
            "Package exists on the registry",
            bool(sv.get("package_exists")),
            True,
            evidence={"registry": sv.get("registry"), "name": sv.get("package_name")},
        ),
        _gate(
            "version_resolved",
            "A published version resolves",
            bool(sv.get("resolved_version")),
            True,
            evidence={"resolved_version": sv.get("resolved_version")},
        ),
        _gate(
            "registry_reachable",
            "Registry was reachable",
            sstatus != "unavailable",
            True,
            reason="registry unavailable — retry" if sstatus == "unavailable" else "",
        ),
        _gate(
            "registry_consistent",
            "Registry does not contradict the manifest",
            sstatus != "mismatch",
            True,
            reason=("; ".join(sv.get("errors") or []) if sstatus == "mismatch" else ""),
        ),
        _gate(
            "repo_consistency",
            "source_repo matches registry metadata",
            sv.get("repo_consistency") != "mismatch",
            True,
            reason=(
                "source_repo != registry repo"
                if sv.get("repo_consistency") == "mismatch"
                else ""
            ),
            evidence={"repo_consistency": sv.get("repo_consistency")},
        ),
        _gate(
            "version_pinned",
            "Launch command pins an exact version",
            sv.get("command_pinning") == "pinned",
            True,
            reason=(
                "command is not pinned to an exact version"
                if sv.get("command_pinning") != "pinned"
                else ""
            ),
            evidence={"command_pinning": sv.get("command_pinning")},
        ),
        _gate(
            "no_high_severity_findings",
            "No high-severity findings declared",
            not high,
            True,
            reason=(f"{len(high)} high-severity finding(s)" if high else ""),
            evidence={"high_codes": [a.get("code") for a in high]},
        ),
        _gate(
            "no_typosquat",
            "Name is not a typosquat of an existing package",
            not typosquat_hit,
            True,
            reason=("name is similar to an existing package" if typosquat_hit else ""),
        ),
        _gate(
            "credentialed_policy_ok",
            "Credential/egress policy satisfied",
            (not credentialed) or sstatus != "mismatch",
            True,
            evidence={"credentialed": credentialed},
        ),
        # --- FUTURE gates: mechanism not built (Slice 2b / 2c). Always blocking,
        # never passing today -> auto_publish_eligible stays False. ---
        _gate(
            "ownership_automatically_proven",
            "Ownership proven by an automated, unforgeable method",
            ownership_method in AUTOMATED_OWNERSHIP_METHODS,
            True,
            reason="no automated ownership proof yet (npm provenance / PyPI Trusted "
            "Publishing / verified maintainer) — only manual admin attestation exists",
            evidence={"ownership_method": ownership_method},
            future=True,
        ),
        _gate(
            "sandbox_smoke",
            "Server ran the MCP in the sandbox and it responded",
            False,
            True,
            reason="server-authoritative sandbox smoke not built yet",
            evidence={"ran": False},
            future=True,
        ),
        # --- ADVISORY: the client's self-attestation. Recorded, never blocking. ---
        _gate(
            "client_tested",
            "Maintainer attested the server as TESTED",
            report.get("status") == "TESTED",
            False,
            evidence={"report_status": report.get("status")},
        ),
    ]

    blocking = [g for g in gates if g["blocking"]]
    failed_blocking = [g for g in blocking if not g["passed"]]
    objective_blockers = [g["id"] for g in failed_blocking if not g["future"]]
    future_blockers = [g["id"] for g in failed_blocking if g["future"]]
    advisory = [g for g in gates if not g["blocking"]]

    auto_publish_eligible = not failed_blocking  # all blocking gates pass

    if auto_publish_eligible:
        reason = "all gates pass"
    elif not objective_blockers:
        reason = (
            "objectively clean — would be eligible once the automated ownership "
            "and sandbox-smoke gates are built"
        )
    else:
        reason = "blocked by: " + ", ".join(objective_blockers)

    return {
        "auto_publish_eligible": auto_publish_eligible,
        "objective_blockers": objective_blockers,
        "future_blockers": future_blockers,
        "review_fallback_reason": reason,
        "advisory": [g["id"] for g in advisory],
        "gates": gates,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
