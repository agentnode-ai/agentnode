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
    ownership: dict | None = None,
    smoke: dict | None = None,
) -> dict:
    """Evaluate the hard + advisory gates for a submission. Pure; returns a dict
    ready to store in the ``server_verification`` JSONB (no migration).

    ``ownership`` is the derived ownership evidence (see mcp.ownership); when
    absent it defaults to 'no proof'. The ownership gate passes only for a STRONG,
    verified proof — none exists today (2b-2+ produce them), so it stays False.

    ``smoke`` is the SmokeResult (see mcp.smoke) from a server-authoritative
    sandbox run; when absent it defaults to 'not run' -> the sandbox_smoke gate
    stays a future blocker. No mechanism produces a real smoke yet (2c-2+), so it
    stays False today too.
    """
    from app.mcp.ownership import derive_ownership_evidence
    from app.mcp.smoke import derive_smoke_evidence

    report = report or {}
    sv = server_verification or {}
    ownership = ownership or derive_ownership_evidence(None, "missing")
    smoke_ev = derive_smoke_evidence(smoke)
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
        # --- Ownership gate: reads the derived evidence (Slice 2b-1 wiring).
        # Passes ONLY for a STRONG, verified proof. No mechanism produces strong
        # evidence yet (2b-2+), so it stays False today -> still a future blocker.
        _gate(
            "ownership_automatically_proven",
            "Ownership proven by an automated, unforgeable method",
            bool(ownership.get("auto_eligible")),
            True,
            reason=ownership.get("reason", ""),
            evidence={
                "confidence": ownership.get("confidence"),
                "method": ownership.get("method"),
                "status": ownership.get("status"),
                **(ownership.get("evidence") or {}),
            },
            # 'future' = the gate cannot pass yet because no mechanism produces a
            # strong proof. Once a strong claim can exist (2b-2), remove this flag.
            future=ownership.get("confidence") != "strong",
        ),
        # --- Sandbox smoke gate: reads the derived SmokeResult (Slice 2c-1 wiring).
        # Passes ONLY for a real, passed smoke. No executor produces one yet
        # (2c-2 npm / 2c-3 PyPI), so with the default 'not_run' result it stays a
        # future blocker — identical to the hardcoded gate it replaces. Always
        # blocking. 'future' is False only once a real result decides it.
        _gate(
            "sandbox_smoke",
            "Server ran the MCP in the sandbox and it responded",
            smoke_ev["passed"],
            True,
            reason=smoke_ev["reason"],
            evidence=smoke_ev["evidence"],
            future=smoke_ev["future"],
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
