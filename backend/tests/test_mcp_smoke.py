"""Slice 2c-1 — sandbox-smoke result model + gate wiring (advisory only).

The safety line: with no smoke result the sandbox_smoke gate is a future blocker
(unchanged from today), so nothing is auto-eligible. A real PASSED smoke can make
the gate pass — and ONLY then, combined with strong ownership and all other gates
clean, does auto_publish_eligible become True. Even then this is a pure advisory
flag: no code publishes on it (publish stays admin-only).

No execution here: derive_smoke_evidence is pure, and evaluate_gates takes the
SmokeResult as data. No container, no install, no network, no migration.
"""

from __future__ import annotations

from app.mcp.gates import evaluate_gates
from app.mcp.ownership import derive_ownership_evidence
from app.mcp.smoke import derive_smoke_evidence


# --- pure derive_smoke_evidence ---------------------------------------------


def test_no_result_is_not_run_future():
    ev = derive_smoke_evidence(None)
    assert ev["status"] == "not_run"
    assert ev["passed"] is False
    assert ev["future"] is True
    assert ev["ran"] is False


def test_passed_result_is_pass_not_future():
    ev = derive_smoke_evidence(
        {
            "status": "passed",
            "runtime": "npm",
            "package": "@scope/mcp",
            "version": "1.2.3",
            "initialized": True,
            "tools_count": 7,
            "duration_ms": 2140,
            "sandbox_backend": "docker",
            "image_digest": "sha256:6c77",
        }
    )
    assert ev["passed"] is True
    assert ev["future"] is False
    assert ev["ran"] is True
    assert ev["evidence"]["tools_count"] == 7
    assert ev["evidence"]["initialized"] is True


def test_failed_initialize_is_hard_objective_blocker():
    ev = derive_smoke_evidence(
        {"status": "failed", "failure_reason": "initialize_failed"}
    )
    assert ev["passed"] is False
    assert ev["future"] is False  # hard failure -> objective blocker, not future
    assert "initialize" in ev["reason"].lower()


def test_failed_tools_list_is_hard_objective_blocker():
    ev = derive_smoke_evidence(
        {"status": "failed", "failure_reason": "tools_list_failed"}
    )
    assert ev["passed"] is False
    assert ev["future"] is False


def test_failed_install_is_transient_review_fallback():
    ev = derive_smoke_evidence({"status": "failed", "failure_reason": "install_failed"})
    assert ev["passed"] is False
    assert ev["future"] is True  # transient -> retry/review, not an objective fault
    assert ev["review_fallback"] is True


def test_unavailable_is_review_fallback_future():
    ev = derive_smoke_evidence({"status": "unavailable"})
    assert ev["passed"] is False
    assert ev["future"] is True
    assert ev["review_fallback"] is True


def test_skipped_credentialed_is_review_fallback():
    ev = derive_smoke_evidence({"status": "skipped", "review_reason": "credentialed"})
    assert ev["passed"] is False
    assert ev["future"] is True
    assert ev["review_fallback"] is True
    assert "credential" in ev["reason"].lower()


def test_skipped_private_is_review_fallback():
    ev = derive_smoke_evidence({"status": "skipped", "review_reason": "private"})
    assert ev["future"] is True
    assert "private" in ev["reason"].lower()


# --- gate wiring -------------------------------------------------------------


def _clean_sv():
    return {
        "server_status": "verified",
        "registry": "npm",
        "package_name": "@scope/mcp",
        "package_exists": True,
        "resolved_version": "1.2.3",
        "repo_consistency": "match",
        "command_pinning": "pinned",
        "errors": [],
    }


def _manifest():
    return {
        "runtime": "mcp",
        "package_id": "scope-mcp",
        "mcp_server": {
            "command": ["npx", "-y", "@scope/mcp@1.2.3"],
            "npm_package": "@scope/mcp",
        },
    }


def _report():
    return {"status": "TESTED", "actions": []}


def _gate(result, gid):
    return next(g for g in result["gates"] if g["id"] == gid)


def test_no_smoke_keeps_gate_future_blocked_unchanged():
    """Default behavior identical to before 2c-1: sandbox_smoke is a future blocker."""
    r = evaluate_gates(
        manifest=_manifest(),
        server_verification=_clean_sv(),
        report=_report(),
        typosquat_hit=False,
    )
    g = _gate(r, "sandbox_smoke")
    assert g["passed"] is False
    assert g["blocking"] is True
    assert g["future"] is True
    assert "sandbox_smoke" in r["future_blockers"]
    assert r["auto_publish_eligible"] is False


def test_smoke_unavailable_blocks_and_stays_review_fallback():
    r = evaluate_gates(
        manifest=_manifest(),
        server_verification=_clean_sv(),
        report=_report(),
        typosquat_hit=False,
        smoke={"status": "unavailable"},
    )
    g = _gate(r, "sandbox_smoke")
    assert g["passed"] is False
    assert g["future"] is True  # infra gap -> future/review, not objective
    assert "sandbox_smoke" in r["future_blockers"]
    assert r["auto_publish_eligible"] is False


def test_smoke_failed_initialize_is_objective_blocker():
    r = evaluate_gates(
        manifest=_manifest(),
        server_verification=_clean_sv(),
        report=_report(),
        typosquat_hit=False,
        smoke={"status": "failed", "failure_reason": "initialize_failed"},
    )
    g = _gate(r, "sandbox_smoke")
    assert g["passed"] is False
    assert g["future"] is False
    assert "sandbox_smoke" in r["objective_blockers"]
    assert r["auto_publish_eligible"] is False


def test_smoke_failed_tools_list_is_objective_blocker():
    r = evaluate_gates(
        manifest=_manifest(),
        server_verification=_clean_sv(),
        report=_report(),
        typosquat_hit=False,
        smoke={"status": "failed", "failure_reason": "tools_list_failed"},
    )
    assert "sandbox_smoke" in r["objective_blockers"]
    assert r["auto_publish_eligible"] is False


def test_smoke_passed_makes_gate_pass():
    r = evaluate_gates(
        manifest=_manifest(),
        server_verification=_clean_sv(),
        report=_report(),
        typosquat_hit=False,
        smoke={
            "status": "passed",
            "runtime": "npm",
            "version": "1.2.3",
            "tools_count": 4,
        },
    )
    g = _gate(r, "sandbox_smoke")
    assert g["passed"] is True
    assert g["future"] is False
    assert "sandbox_smoke" not in r["future_blockers"]
    assert "sandbox_smoke" not in r["objective_blockers"]


def test_passed_smoke_plus_strong_ownership_plus_clean_is_finally_eligible():
    """The whole point of the arc: a passed smoke AND strong ownership AND all
    other gates clean is the FIRST time auto_publish_eligible can be True. It is a
    pure advisory flag — no code publishes on it (publish is admin-only)."""
    r = evaluate_gates(
        manifest=_manifest(),
        server_verification=_clean_sv(),
        report=_report(),
        typosquat_hit=False,
        ownership=derive_ownership_evidence("publish_challenge", "verified"),
        smoke={
            "status": "passed",
            "runtime": "npm",
            "version": "1.2.3",
            "tools_count": 4,
        },
    )
    assert _gate(r, "ownership_automatically_proven")["passed"] is True
    assert _gate(r, "sandbox_smoke")["passed"] is True
    assert r["objective_blockers"] == []
    assert r["future_blockers"] == []
    assert r["auto_publish_eligible"] is True


def test_passed_smoke_but_a_real_blocker_is_not_eligible():
    """Passed smoke + strong ownership but a high-severity finding -> still blocked."""
    r = evaluate_gates(
        manifest=_manifest(),
        server_verification=_clean_sv(),
        report={"status": "TESTED", "actions": [{"severity": "high", "code": "X"}]},
        typosquat_hit=False,
        ownership=derive_ownership_evidence("publish_challenge", "verified"),
        smoke={"status": "passed", "runtime": "npm", "version": "1.2.3"},
    )
    assert "no_high_severity_findings" in r["objective_blockers"]
    assert r["auto_publish_eligible"] is False


def test_result_shape_is_json_serializable_with_smoke():
    import json

    r = evaluate_gates(
        manifest=_manifest(),
        server_verification=_clean_sv(),
        report=_report(),
        typosquat_hit=False,
        smoke={
            "status": "passed",
            "runtime": "npm",
            "version": "1.2.3",
            "tools_count": 2,
        },
    )
    json.dumps(r)  # must not raise (stored in JSONB)
