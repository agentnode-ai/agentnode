"""Slice 2a — MCP auto-publish gate evaluator (advisory only).

The safety line: no input may make auto_publish_eligible true today, because the
ownership + sandbox-smoke gates are blocking and cannot pass yet. The evaluator
still makes visible which submissions are objectively clean (blocked ONLY by
those future gates).
"""

from __future__ import annotations

from app.mcp.gates import evaluate_gates


def _clean_sv():
    return {
        "server_status": "verified",
        "registry": "npm",
        "package_name": "@scope/mcp",
        "package_exists": True,
        "resolved_version": "1.2.3",
        "version_exists": True,
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


def _clean_report():
    return {"status": "TESTED", "actions": []}


def _ids(result, key):
    return set(result[key])


def _gate(result, gid):
    return next(g for g in result["gates"] if g["id"] == gid)


# --- the core safety invariant ----------------------------------------------


def test_never_eligible_today_even_when_objectively_clean():
    r = evaluate_gates(
        manifest=_manifest(),
        server_verification=_clean_sv(),
        report=_clean_report(),
        typosquat_hit=False,
    )
    # Objectively clean: no real blockers...
    assert r["objective_blockers"] == []
    # ...but the two future gates keep it not-eligible.
    assert r["auto_publish_eligible"] is False
    assert set(r["future_blockers"]) == {
        "ownership_automatically_proven",
        "sandbox_smoke",
    }
    # Honest reason (2c-5): pending a proof/result, not "gates not built".
    assert "pending" in r["review_fallback_reason"]
    assert "ownership" in r["review_fallback_reason"]


def test_future_gates_are_blocking_and_never_pass():
    from app.mcp.ownership import derive_ownership_evidence

    r = evaluate_gates(
        manifest=_manifest(),
        server_verification=_clean_sv(),
        report=_clean_report(),
        typosquat_hit=False,
        # admin attestation is NOT an automated proof -> ownership gate stays False
        ownership=derive_ownership_evidence("manual_admin", "verified"),
    )
    assert _gate(r, "ownership_automatically_proven")["passed"] is False
    assert _gate(r, "ownership_automatically_proven")["blocking"] is True
    assert _gate(r, "sandbox_smoke")["passed"] is False
    assert _gate(r, "sandbox_smoke")["blocking"] is True


# --- client TESTED is advisory, never blocking ------------------------------


def test_client_tested_is_advisory_only():
    r = evaluate_gates(
        manifest=_manifest(),
        server_verification=_clean_sv(),
        report=_clean_report(),
        typosquat_hit=False,
    )
    assert "client_tested" in r["advisory"]
    assert _gate(r, "client_tested")["blocking"] is False
    # A missing TESTED report does NOT add an objective blocker.
    r2 = evaluate_gates(
        manifest=_manifest(),
        server_verification=_clean_sv(),
        report={"status": "REVIEW_NEEDED", "actions": []},
        typosquat_hit=False,
    )
    assert r2["objective_blockers"] == []
    assert _gate(r2, "client_tested")["passed"] is False


def test_client_tested_alone_is_never_enough():
    # Only the client report is good; the server facts are missing.
    r = evaluate_gates(
        manifest=_manifest(),
        server_verification={"server_status": "unavailable", "package_exists": False},
        report=_clean_report(),
        typosquat_hit=False,
    )
    assert r["auto_publish_eligible"] is False
    assert "package_exists" in r["objective_blockers"]


# --- individual blocking gates ----------------------------------------------


def test_high_severity_finding_blocks():
    r = evaluate_gates(
        manifest=_manifest(),
        server_verification=_clean_sv(),
        report={"status": "TESTED", "actions": [{"severity": "high", "code": "X"}]},
        typosquat_hit=False,
    )
    assert "no_high_severity_findings" in r["objective_blockers"]


def test_registry_mismatch_blocks():
    sv = _clean_sv()
    sv["server_status"] = "mismatch"
    sv["errors"] = ["source_repo points elsewhere"]
    r = evaluate_gates(
        manifest=_manifest(),
        server_verification=sv,
        report=_clean_report(),
        typosquat_hit=False,
    )
    assert "registry_consistent" in r["objective_blockers"]


def test_unpinned_version_blocks():
    sv = _clean_sv()
    sv["command_pinning"] = "unpinned_resolved"
    r = evaluate_gates(
        manifest=_manifest(),
        server_verification=sv,
        report=_clean_report(),
        typosquat_hit=False,
    )
    assert "version_pinned" in r["objective_blockers"]


def test_typosquat_blocks():
    r = evaluate_gates(
        manifest=_manifest(),
        server_verification=_clean_sv(),
        report=_clean_report(),
        typosquat_hit=True,
    )
    assert "no_typosquat" in r["objective_blockers"]


def test_repo_mismatch_blocks():
    sv = _clean_sv()
    sv["repo_consistency"] = "mismatch"
    r = evaluate_gates(
        manifest=_manifest(),
        server_verification=sv,
        report=_clean_report(),
        typosquat_hit=False,
    )
    assert "repo_consistency" in r["objective_blockers"]


def test_missing_package_blocks():
    sv = _clean_sv()
    sv["package_exists"] = False
    sv["resolved_version"] = None
    r = evaluate_gates(
        manifest=_manifest(),
        server_verification=sv,
        report=_clean_report(),
        typosquat_hit=False,
    )
    assert "package_exists" in r["objective_blockers"]
    assert "version_resolved" in r["objective_blockers"]


def test_result_shape_is_json_serializable():
    import json

    r = evaluate_gates(
        manifest=_manifest(),
        server_verification=_clean_sv(),
        report=_clean_report(),
        typosquat_hit=False,
    )
    json.dumps(r)  # must not raise (stored in JSONB)
    for key in (
        "auto_publish_eligible",
        "gates",
        "blockers" if False else "objective_blockers",
        "future_blockers",
        "review_fallback_reason",
        "advisory",
        "checked_at",
    ):
        assert key in r


# --- 2c-5 lock-in: eligibility is advisory; publish stays admin-only ----------


def test_full_eligibility_is_advisory_data_only():
    """Strong ownership + fresh passing smoke + clean gates -> auto_publish_eligible
    True, but evaluate_gates is a PURE function returning data — it publishes
    nothing. (Freshness matches by giving the smoke the same binding keys.)"""
    from datetime import datetime, timedelta, timezone

    from app.mcp.ownership import derive_ownership_evidence

    now = datetime(2026, 7, 14, tzinfo=timezone.utc)
    keys = {
        "runtime": "npm",
        "package": "@scope/mcp",
        "version": "1.2.3",
        "command_hash": "h",
        "image_digest": "sha256:img",
        "run_model": "npx_offline",
        "schema_version": 1,
    }
    smoke = {
        **keys,
        "status": "passed",
        "expires_at": (now + timedelta(days=10)).isoformat(),
    }
    r = evaluate_gates(
        manifest=_manifest(),
        server_verification=_clean_sv(),
        report=_clean_report(),
        typosquat_hit=False,
        ownership=derive_ownership_evidence("publish_challenge", "verified"),
        smoke=smoke,
        smoke_keys=keys,
        now=now,
    )
    assert r["auto_publish_eligible"] is True
    # It is just data (advisory) — no status, no publish side effect here.
    assert set(r) >= {"auto_publish_eligible", "gates", "review_fallback_reason"}


def test_publish_submission_is_admin_only_and_ignores_eligibility():
    """Lock-in: the ONLY publish path is admin-gated and never keys on
    auto_publish_eligible. Guards against a future accidental auto-publish."""
    import inspect

    from app.mcp import router

    src = inspect.getsource(router.publish_submission)
    assert "require_admin" in src  # admin-only
    assert "auto_publish_eligible" not in src  # never triggers on eligibility
