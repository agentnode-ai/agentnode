"""Slice 2a — MCP auto-publish gate evaluator (advisory only, activates nothing).

A PURE function that turns the server-derived facts + the advisory client report
into a structured, evidenced ``GateResult``. It makes visible which submissions
would be auto-publish-eligible, which gates block, which are advisory, and why a
submission stays in review — WITHOUT changing any status, without a migration,
and without auto-publishing anything.

Two gates depend on evidence that must be produced per submission: an automated
ownership proof (2b — publish-challenge etc.) and a server-authoritative sandbox
smoke (2c — npm/PyPI executors, host-verified). The MECHANISMS are built; they
pass only once a submission actually has a STRONG verified ownership claim AND a
FRESH passing smoke. Until then they block. ``objective_blockers`` reports the
blockers EXCLUDING those two (which carry ``future=True`` while pending a
result/proof) — so a submission whose only remaining blockers are those reads as
"objectively clean, pending ownership + smoke". ``auto_publish_eligible`` can
therefore compute True (strong ownership + fresh passing smoke + all other gates
clean), but it is ADVISORY ONLY: no code publishes on it — publish is admin-only
(see mcp.router.publish_submission). In prod the smoke is disabled by default, so
no smoke result exists and this stays False for real submissions.

No I/O here: the caller supplies ``typosquat_hit`` (and optionally the ownership
claim, the smoke result, and the current binding keys) so this stays a pure,
table-testable function.
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
    smoke_keys: dict | None = None,
    now=None,
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
    from app.mcp.smoke import derive_smoke_evidence, evaluate_smoke_freshness

    report = report or {}
    sv = server_verification or {}
    ownership = ownership or derive_ownership_evidence(None, "missing")
    # 2c-4a: a passed smoke only counts while fresh. When the caller supplies the
    # current submission's binding keys, downgrade a stale passed smoke to
    # expired/key_mismatch (review / recheck-needed) so the gate stops passing.
    freshness = None
    if smoke and smoke_keys is not None:
        now = now or datetime.now(timezone.utc)
        freshness = evaluate_smoke_freshness(smoke, smoke_keys, now)
    smoke_ev = derive_smoke_evidence(smoke, freshness=freshness)
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
            # 'future' = pending a proof: the mechanism exists (2b), but this claim
            # is not a STRONG verified proof yet, so the gate blocks as future.
            future=ownership.get("confidence") != "strong",
        ),
        # --- Sandbox smoke gate: reads the derived SmokeResult (2c-1 wiring +
        # 2c-4a freshness). The npm/PyPI executors exist (2c-2/2c-3, host-verified)
        # but are inert unless MCP_SMOKE_MODE=container, so with the default
        # 'not_run' result it stays a future blocker. Always blocking; 'future' is
        # False only once a real, fresh result decides it (a fresh pass, or a hard
        # failure -> objective).
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
            "objectively clean — pending an automated ownership proof and a "
            "passing sandbox smoke"
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
