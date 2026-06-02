"""Tests for MCP Self-Service Flow: submit, status, admin review, duplicate detection."""
import pytest
import pytest_asyncio

from tests.conftest import register_and_login, create_publisher, setup_publisher_user
from app.mcp.models import McpSubmission  # noqa: F401


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

MCP_MANIFEST = {
    "manifest_version": "0.3",
    "package_id": "mcp-test-server",
    "name": "Test MCP Server",
    "package_type": "toolpack",
    "runtime": "mcp",
    "install_mode": "package",
    "version": "0.1.0",
    "visibility": "public",
    "publisher": "pub-a",
    "summary": "A test MCP server",
    "mcp_server": {
        "command": ["npx", "-y", "test-mcp@1.0.0"],
        "transport": "stdio",
        "npm_package": "test-mcp",
        "source_repo": "https://github.com/test/test-mcp",
        "env_keys": [],
    },
    "capabilities": {"tools": [], "resources": [], "prompts": []},
    "permissions": {
        "network": {"level": "none"},
        "filesystem": {"level": "none"},
        "code_execution": {"level": "none"},
    },
    "tags": ["mcp"],
    "categories": ["general"],
    "compatibility": {"frameworks": ["mcp"]},
}

TESTED_REPORT = {
    "status": "TESTED",
    "summary": "test-mcp: all checks passed.",
    "manifest_version": "0.3",
    "package": {"registry": "npm", "name": "test-mcp", "version": "1.0.0"},
    "checks": [{"name": "schema", "passed": True, "detail": "v0.3"}],
    "actions": [],
    "permissions": {"declared": {"network": "none", "filesystem": "none", "code_execution": "none"}},
    "tools_snapshot": [{"name": "test_tool", "description": "A test tool"}],
    "risk_flags": [],
    "warnings": [],
    "errors": [],
}

INVALID_REPORT = {**TESTED_REPORT, "status": "INVALID", "errors": ["schema error"]}

ACTION_REQUIRED_REPORT = {
    **TESTED_REPORT,
    "status": "MAINTAINER_ACTION_REQUIRED",
    "actions": [{"severity": "high", "code": "OWNER_METADATA_MISMATCH", "title": "Fix", "detail": "mismatch", "fix": "fix it"}],
}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. Submit Auth
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_submit_no_auth(client):
    resp = await client.post("/v1/mcp/submit", json={"manifest": MCP_MANIFEST, "verification_report": TESTED_REPORT})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_submit_user_without_publisher(client):
    token = await register_and_login(client, "nopub@test.dev", "nopub", "TestPass123!")
    resp = await client.post("/v1/mcp/submit", json={"manifest": MCP_MANIFEST, "verification_report": TESTED_REPORT}, headers=_auth(token))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_submit_with_publisher(client):
    token, _ = await setup_publisher_user(client, "puba@test.dev", "puba", "TestPass123!", "pub-a", "Publisher A")
    resp = await client.post("/v1/mcp/submit", json={"manifest": MCP_MANIFEST, "verification_report": TESTED_REPORT}, headers=_auth(token))
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "pending"
    assert data["package_name"] == "test-mcp"


# ---------------------------------------------------------------------------
# 2. Submit Validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_submit_invalid_report_blocked(client):
    token, _ = await setup_publisher_user(client, "val1@test.dev", "val1", "TestPass123!", "pub-val1", "Val1")
    resp = await client.post("/v1/mcp/submit", json={"manifest": MCP_MANIFEST, "verification_report": INVALID_REPORT}, headers=_auth(token))
    assert resp.status_code == 400
    assert "INVALID" in resp.json()["error"]["code"]


@pytest.mark.asyncio
async def test_submit_action_required_accepted(client):
    token, _ = await setup_publisher_user(client, "val2@test.dev", "val2", "TestPass123!", "pub-val2", "Val2")
    resp = await client.post("/v1/mcp/submit", json={"manifest": MCP_MANIFEST, "verification_report": ACTION_REQUIRED_REPORT}, headers=_auth(token))
    assert resp.status_code == 201
    assert resp.json()["status"] == "action_required"


@pytest.mark.asyncio
async def test_submit_non_mcp_manifest_blocked(client):
    token, _ = await setup_publisher_user(client, "val3@test.dev", "val3", "TestPass123!", "pub-val3", "Val3")
    bad_manifest = {**MCP_MANIFEST, "runtime": "python"}
    resp = await client.post("/v1/mcp/submit", json={"manifest": bad_manifest, "verification_report": TESTED_REPORT}, headers=_auth(token))
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 3. Duplicate Detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_submission_blocked(client):
    token, _ = await setup_publisher_user(client, "dup@test.dev", "dup", "TestPass123!", "pub-dup", "Dup")
    resp1 = await client.post("/v1/mcp/submit", json={"manifest": MCP_MANIFEST, "verification_report": TESTED_REPORT}, headers=_auth(token))
    assert resp1.status_code == 201

    resp2 = await client.post("/v1/mcp/submit", json={"manifest": MCP_MANIFEST, "verification_report": TESTED_REPORT}, headers=_auth(token))
    assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_different_publisher_same_package_allowed(client):
    token_a, _ = await setup_publisher_user(client, "dupa@test.dev", "dupa", "TestPass123!", "pub-dupa", "Dup A")
    token_b, _ = await setup_publisher_user(client, "dupb@test.dev", "dupb", "TestPass123!", "pub-dupb", "Dup B")

    resp1 = await client.post("/v1/mcp/submit", json={"manifest": MCP_MANIFEST, "verification_report": TESTED_REPORT}, headers=_auth(token_a))
    assert resp1.status_code == 201

    resp2 = await client.post("/v1/mcp/submit", json={"manifest": MCP_MANIFEST, "verification_report": TESTED_REPORT}, headers=_auth(token_b))
    assert resp2.status_code == 201


# ---------------------------------------------------------------------------
# 4. Maintainer Status Endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_maintainer_can_read_own_submission(client):
    token, _ = await setup_publisher_user(client, "own@test.dev", "own", "TestPass123!", "pub-own", "Own")
    submit_resp = await client.post("/v1/mcp/submit", json={"manifest": MCP_MANIFEST, "verification_report": TESTED_REPORT}, headers=_auth(token))
    sub_id = submit_resp.json()["id"]

    status_resp = await client.get(f"/v1/mcp/submissions/{sub_id}", headers=_auth(token))
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["status"] == "pending"
    assert data["package_name"] == "test-mcp"


@pytest.mark.asyncio
async def test_other_publisher_cannot_read_submission(client):
    token_a, _ = await setup_publisher_user(client, "reada@test.dev", "reada", "TestPass123!", "pub-reada", "Read A")
    token_b, _ = await setup_publisher_user(client, "readb@test.dev", "readb", "TestPass123!", "pub-readb", "Read B")

    submit_resp = await client.post("/v1/mcp/submit", json={"manifest": MCP_MANIFEST, "verification_report": TESTED_REPORT}, headers=_auth(token_a))
    sub_id = submit_resp.json()["id"]

    status_resp = await client.get(f"/v1/mcp/submissions/{sub_id}", headers=_auth(token_b))
    assert status_resp.status_code == 404


@pytest.mark.asyncio
async def test_status_does_not_leak_reviewer_notes(client, session):
    token, _ = await setup_publisher_user(client, "leak@test.dev", "leak", "TestPass123!", "pub-leak", "Leak")
    submit_resp = await client.post("/v1/mcp/submit", json={"manifest": MCP_MANIFEST, "verification_report": TESTED_REPORT}, headers=_auth(token))
    sub_id = submit_resp.json()["id"]

    # Simulate admin setting reviewer_notes directly in DB
    from sqlalchemy import text
    await session.execute(text(
        "UPDATE mcp_submissions SET reviewer_notes = :notes, maintainer_feedback = :fb WHERE id = :sid"
    ), {"notes": "INTERNAL: suspicious publisher", "fb": "Please fix URLs", "sid": sub_id})
    await session.commit()

    status_resp = await client.get(f"/v1/mcp/submissions/{sub_id}", headers=_auth(token))
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data.get("maintainer_feedback") == "Please fix URLs"
    assert "reviewer_notes" not in data
    assert "INTERNAL" not in str(data)
    assert "reviewed_by_id" not in data


# ---------------------------------------------------------------------------
# 5. Admin Review
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_admin_cannot_list_submissions(client):
    token, _ = await setup_publisher_user(client, "nonadm@test.dev", "nonadm", "TestPass123!", "pub-nonadm", "NonAdm")
    resp = await client.get("/v1/admin/mcp/submissions", headers=_auth(token))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_review(client, session):
    # Create publisher + submission
    token, _ = await setup_publisher_user(client, "admrev@test.dev", "admrev", "TestPass123!", "pub-admrev", "AdmRev")
    submit_resp = await client.post("/v1/mcp/submit", json={"manifest": MCP_MANIFEST, "verification_report": TESTED_REPORT}, headers=_auth(token))
    sub_id = submit_resp.json()["id"]

    # Create admin
    admin_token = await register_and_login(client, "admin-mcp@test.dev", "adminmcp", "AdminPass123!")
    from sqlalchemy import update as sa_update
    from app.auth.models import User
    await session.execute(sa_update(User).where(User.username == "adminmcp").values(is_admin=True))
    await session.commit()

    # Review
    review_resp = await client.put(
        f"/v1/admin/mcp/submissions/{sub_id}/review",
        json={"status": "approved", "notes": "Looks good internally", "maintainer_feedback": "Approved for catalog."},
        headers=_auth(admin_token),
    )
    assert review_resp.status_code == 200
    assert review_resp.json()["status"] == "approved"

    # Maintainer sees feedback but not internal notes
    status_resp = await client.get(f"/v1/mcp/submissions/{sub_id}", headers=_auth(token))
    data = status_resp.json()
    assert data["status"] == "approved"
    assert data["maintainer_feedback"] == "Approved for catalog."
    assert "Looks good internally" not in str(data)
