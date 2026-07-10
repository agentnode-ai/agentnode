"""Slice 2b-1 — ownership evidence model + gate wiring (framework, activates nothing).

Confirms: no/weak/medium/admin evidence never satisfies the ownership gate;
STRONG + verified CAN satisfy it; but auto_publish_eligible stays False while
sandbox_smoke is a future blocker. No mechanism produces strong evidence today —
these strong cases are simulated to prove the wiring, not a live capability.
"""

from __future__ import annotations

from app.mcp import ownership as own
from app.mcp.gates import evaluate_gates


def _manifest():
    return {
        "runtime": "mcp",
        "package_id": "scope-mcp",
        "mcp_server": {
            "command": ["npx", "-y", "@scope/mcp@1.2.3"],
            "npm_package": "@scope/mcp",
        },
    }


def _clean_sv():
    return {
        "server_status": "verified",
        "package_exists": True,
        "resolved_version": "1.2.3",
        "repo_consistency": "match",
        "command_pinning": "pinned",
        "errors": [],
    }


def _clean_report():
    return {"status": "TESTED", "actions": []}


def _gate(result, gid):
    return next(g for g in result["gates"] if g["id"] == gid)


# --- the evidence model ------------------------------------------------------


def test_method_confidence_taxonomy():
    assert own.method_confidence("publish_challenge") == own.STRONG
    assert own.method_confidence("npm_provenance_repo_control") == own.STRONG
    assert own.method_confidence("manual_admin") == own.ADMIN_ATTESTED
    assert own.method_confidence("registry_metadata_match") == own.MEDIUM
    assert own.method_confidence("something_else") == own.WEAK
    assert own.method_confidence(None) == own.NONE


def test_auto_eligible_only_strong_and_verified():
    assert (
        own.derive_ownership_evidence("publish_challenge", "verified")["auto_eligible"]
        is True
    )
    # strong but not verified (expired/revoked) -> not eligible
    assert (
        own.derive_ownership_evidence("publish_challenge", "expired")["auto_eligible"]
        is False
    )
    # admin attestation -> never auto
    assert (
        own.derive_ownership_evidence("manual_admin", "verified")["auto_eligible"]
        is False
    )
    # medium / weak / none -> never auto
    assert (
        own.derive_ownership_evidence("registry_metadata_match", "verified")[
            "auto_eligible"
        ]
        is False
    )
    assert own.derive_ownership_evidence(None, "missing")["auto_eligible"] is False


# --- gate wiring -------------------------------------------------------------


def _eval(ownership):
    return evaluate_gates(
        manifest=_manifest(),
        server_verification=_clean_sv(),
        report=_clean_report(),
        typosquat_hit=False,
        ownership=ownership,
    )


def test_no_evidence_blocks_ownership_gate():
    r = _eval(own.derive_ownership_evidence(None, "missing"))
    g = _gate(r, "ownership_automatically_proven")
    assert g["passed"] is False
    assert g["future"] is True  # no mechanism yet
    assert "ownership_automatically_proven" in r["future_blockers"]
    assert r["auto_publish_eligible"] is False


def test_admin_attested_is_not_auto():
    r = _eval(own.derive_ownership_evidence("manual_admin", "verified"))
    g = _gate(r, "ownership_automatically_proven")
    assert g["passed"] is False
    assert g["evidence"]["confidence"] == own.ADMIN_ATTESTED
    assert r["auto_publish_eligible"] is False


def test_medium_and_weak_block():
    for method in ("registry_metadata_match", "something_else"):
        r = _eval(own.derive_ownership_evidence(method, "verified"))
        assert _gate(r, "ownership_automatically_proven")["passed"] is False
        assert r["auto_publish_eligible"] is False


def test_strong_verified_passes_ownership_gate_but_not_auto_publish():
    """The wiring works: a STRONG, verified proof satisfies the ownership gate.
    But sandbox_smoke is still a future blocker, so auto_publish stays False —
    the safety line holds even with ownership solved."""
    r = _eval(own.derive_ownership_evidence("publish_challenge", "verified"))
    g = _gate(r, "ownership_automatically_proven")
    assert g["passed"] is True
    assert g["future"] is False  # a real proof exists -> no longer a future gate
    # ownership no longer blocks; but sandbox_smoke still does.
    assert "ownership_automatically_proven" not in r["objective_blockers"]
    assert "ownership_automatically_proven" not in r["future_blockers"]
    assert "sandbox_smoke" in r["future_blockers"]
    assert r["auto_publish_eligible"] is False


def test_strong_but_expired_is_an_objective_blocker():
    r = _eval(own.derive_ownership_evidence("publish_challenge", "expired"))
    g = _gate(r, "ownership_automatically_proven")
    assert g["passed"] is False
    assert (
        g["future"] is False
    )  # strong mechanism, but the claim lapsed -> real blocker
    assert "ownership_automatically_proven" in r["objective_blockers"]


def test_client_tested_still_advisory():
    r = _eval(own.derive_ownership_evidence("publish_challenge", "verified"))
    assert "client_tested" in r["advisory"]
    assert _gate(r, "client_tested")["blocking"] is False
