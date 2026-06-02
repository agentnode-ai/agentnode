"""Tests for MCP Self-Service Flow: submit, status, admin review, duplicate detection,
and server-side registry re-verification (the trust gate)."""
import copy

import pytest
import pytest_asyncio

from tests.conftest import register_and_login, create_publisher, setup_publisher_user
from app.mcp.models import McpSubmission  # noqa: F401
from app.mcp.registry_verify import RegistryUnavailable


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
    "summary": "A test MCP server for automated testing of the submit flow.",
    "mcp_server": {
        "command": ["npx", "-y", "test-mcp@1.0.0"],
        "transport": "stdio",
        "npm_package": "test-mcp",
        "source_repo": "https://github.com/test/test-mcp",
        "env_keys": [],
    },
    "capabilities": {
        "tools": [{
            "name": "test_tool",
            "capability_id": "general",
            "description": "A test tool",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        }],
        "resources": [],
        "prompts": [],
    },
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
    "checks": [
        {"name": "schema", "passed": True, "detail": "v0.3"},
        {"name": "package_exists", "passed": True, "detail": "test-mcp on npm"},
        {"name": "version_exists", "passed": True, "detail": "1.0.0"},
        {"name": "version_pinned", "passed": True, "detail": "npx -y test-mcp@1.0.0"},
        {"name": "owner_verified", "passed": True, "detail": "test/test-mcp matches registry"},
        {"name": "protocol_test", "passed": True, "detail": "1 tools discovered"},
    ],
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
# Registry mock: server-side verification calls _fetch_npm/_fetch_pypi. We patch
# that boundary so tests are deterministic and never touch the network. The
# hardened httpx client (allowlist/timeouts) is exercised by its own unit test.
# ---------------------------------------------------------------------------

_FAKE_NPM = {
    # Honest package: registry repo matches the manifest source_repo -> verified
    "test-mcp": {
        "versions": {"1.0.0": {"dist": {"shasum": "abc123", "integrity": "sha512-xxx"}}},
        "dist-tags": {"latest": "1.0.0"},
        "repository": {"url": "git+https://github.com/test/test-mcp.git"},
        "maintainers": [{"name": "test"}],
    },
    # Exists, but registry repo points at a DIFFERENT owner than the manifest claims
    "evil-mcp": {
        "versions": {"1.0.0": {"dist": {"shasum": "def456"}}},
        "dist-tags": {"latest": "1.0.0"},
        "repository": {"url": "https://github.com/realowner/evil-mcp"},
        "maintainers": [{"name": "realowner"}],
    },
    # Exists, but registry has no repository URL -> repo_consistency indeterminate
    "norepo-mcp": {
        "versions": {"2.0.0": {"dist": {"shasum": "ghi789"}}},
        "dist-tags": {"latest": "2.0.0"},
        "maintainers": [],
    },
    # "ghost-mcp" is intentionally absent -> npm 404
}


@pytest.fixture(autouse=True)
def mock_registry(monkeypatch):
    async def fake_npm(name):
        return _FAKE_NPM.get(name)  # None => 404 (package does not exist)

    async def fake_pypi(name):
        return None

    monkeypatch.setattr("app.mcp.registry_verify._fetch_npm", fake_npm)
    monkeypatch.setattr("app.mcp.registry_verify._fetch_pypi", fake_pypi)


def _mcp_manifest(npm_package, command, source_repo, package_id):
    m = copy.deepcopy(MCP_MANIFEST)
    m["package_id"] = package_id
    m["mcp_server"]["npm_package"] = npm_package
    m["mcp_server"]["command"] = command
    m["mcp_server"]["source_repo"] = source_repo
    return m


async def _make_admin(client, session, email, username):
    token = await register_and_login(client, email, username, "AdminPass123!")
    from sqlalchemy import update as sa_update
    from app.auth.models import User
    await session.execute(sa_update(User).where(User.username == username).values(is_admin=True))
    await session.commit()
    return token


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


# ---------------------------------------------------------------------------
# 6. Catalog Publication
# ---------------------------------------------------------------------------

async def _create_admin_and_approved_submission(client, session):
    """Helper: create a publisher, submit, admin-approve. Returns (admin_token, publisher_token, sub_id)."""
    pub_token, _ = await setup_publisher_user(client, "pubcat@test.dev", "pubcat", "TestPass123!", "pub-cat", "Pub Cat")
    submit_resp = await client.post("/v1/mcp/submit", json={"manifest": MCP_MANIFEST, "verification_report": TESTED_REPORT}, headers=_auth(pub_token))
    assert submit_resp.status_code == 201
    sub_id = submit_resp.json()["id"]

    admin_token = await register_and_login(client, "admincat@test.dev", "admincat", "AdminPass123!")
    from sqlalchemy import update as sa_update
    from app.auth.models import User
    await session.execute(sa_update(User).where(User.username == "admincat").values(is_admin=True))
    await session.commit()

    review_resp = await client.put(
        f"/v1/admin/mcp/submissions/{sub_id}/review",
        json={"status": "approved"},
        headers=_auth(admin_token),
    )
    assert review_resp.status_code == 200
    return admin_token, pub_token, sub_id


@pytest.mark.asyncio
async def test_publish_approved_submission(client, session):
    admin_token, pub_token, sub_id = await _create_admin_and_approved_submission(client, session)

    resp = await client.post(f"/v1/admin/mcp/submissions/{sub_id}/publish", headers=_auth(admin_token))
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.json()}"
    data = resp.json()
    assert data["package_slug"] == "mcp-test-server"
    assert data["package_id"]

    # Maintainer sees published status
    status_resp = await client.get(f"/v1/mcp/submissions/{sub_id}", headers=_auth(pub_token))
    assert status_resp.json()["published_package_id"] is not None


@pytest.mark.asyncio
async def test_publish_pending_blocked(client, session):
    """Cannot publish a pending (non-approved) submission."""
    pub_token, _ = await setup_publisher_user(client, "pend@test.dev", "pend", "TestPass123!", "pub-pend", "Pend")
    submit_resp = await client.post("/v1/mcp/submit", json={"manifest": MCP_MANIFEST, "verification_report": TESTED_REPORT}, headers=_auth(pub_token))
    sub_id = submit_resp.json()["id"]

    admin_token = await register_and_login(client, "adminpend@test.dev", "adminpend", "AdminPass123!")
    from sqlalchemy import update as sa_update
    from app.auth.models import User
    await session.execute(sa_update(User).where(User.username == "adminpend").values(is_admin=True))
    await session.commit()

    resp = await client.post(f"/v1/admin/mcp/submissions/{sub_id}/publish", headers=_auth(admin_token))
    assert resp.status_code == 400
    assert "approved" in resp.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_publish_resolved_blocked(client, session):
    """Cannot publish a submission with RESOLVED (no protocol test)."""
    resolved_report = {**TESTED_REPORT, "status": "RESOLVED"}
    pub_token, _ = await setup_publisher_user(client, "resol@test.dev", "resol", "TestPass123!", "pub-resol", "Resol")
    submit_resp = await client.post("/v1/mcp/submit", json={"manifest": MCP_MANIFEST, "verification_report": resolved_report}, headers=_auth(pub_token))
    sub_id = submit_resp.json()["id"]

    admin_token = await register_and_login(client, "adminresol@test.dev", "adminresol", "AdminPass123!")
    from sqlalchemy import update as sa_update
    from app.auth.models import User
    await session.execute(sa_update(User).where(User.username == "adminresol").values(is_admin=True))
    await session.commit()

    # Approve first
    await client.put(f"/v1/admin/mcp/submissions/{sub_id}/review", json={"status": "approved"}, headers=_auth(admin_token))

    # Try publish — should fail because RESOLVED not TESTED
    resp = await client.post(f"/v1/admin/mcp/submissions/{sub_id}/publish", headers=_auth(admin_token))
    assert resp.status_code == 400
    assert "TESTED" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_publish_with_high_action_blocked(client, session):
    """Cannot publish a submission with high-severity actions."""
    pub_token, _ = await setup_publisher_user(client, "hact@test.dev", "hact", "TestPass123!", "pub-hact", "HighAct")
    submit_resp = await client.post("/v1/mcp/submit", json={"manifest": MCP_MANIFEST, "verification_report": ACTION_REQUIRED_REPORT}, headers=_auth(pub_token))
    sub_id = submit_resp.json()["id"]

    admin_token = await register_and_login(client, "adminhact@test.dev", "adminhact", "AdminPass123!")
    from sqlalchemy import update as sa_update
    from app.auth.models import User
    await session.execute(sa_update(User).where(User.username == "adminhact").values(is_admin=True))
    await session.commit()

    await client.put(f"/v1/admin/mcp/submissions/{sub_id}/review", json={"status": "approved"}, headers=_auth(admin_token))
    resp = await client.post(f"/v1/admin/mcp/submissions/{sub_id}/publish", headers=_auth(admin_token))
    assert resp.status_code == 400
    assert "high-severity" in resp.json()["error"]["message"]


# ---------------------------------------------------------------------------
# 7. Regression: entrypoint validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_mcp_toolpack_still_requires_entrypoint():
    """Regression: runtime=mcp skips entrypoint validation, but python toolpacks must not."""
    from app.packages.validator import validate_manifest

    non_mcp_manifest = {
        "manifest_version": "0.3",
        "package_id": "test-python-pack",
        "name": "Test Python Pack",
        "package_type": "toolpack",
        "runtime": "python",
        "install_mode": "package",
        "version": "1.0.0",
        "publisher": "test",
        "summary": "A Python toolpack without entrypoint for regression testing.",
        "capabilities": {
            "tools": [{
                "name": "my_tool",
                "capability_id": "general",
                "description": "Test",
                "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
            }],
            "resources": [],
            "prompts": [],
        },
        "permissions": {"network": {"level": "none"}, "filesystem": {"level": "none"}, "code_execution": {"level": "none"}},
        "tags": ["test"],
        "categories": ["general"],
        "compatibility": {"frameworks": ["generic"]},
    }
    valid, errors, _ = await validate_manifest(non_mcp_manifest)
    assert not valid, "Python toolpack without entrypoint should be invalid"
    assert any("entrypoint" in e.lower() for e in errors)

    mcp_manifest = {**non_mcp_manifest, "runtime": "mcp", "package_id": "test-mcp-pack",
                     "mcp_server": {"command": ["npx", "-y", "test@1.0"], "transport": "stdio", "npm_package": "test"}}
    valid_mcp, errors_mcp, _ = await validate_manifest(mcp_manifest)
    entrypoint_errors = [e for e in errors_mcp if "entrypoint" in e.lower()]
    assert not entrypoint_errors, f"MCP should skip entrypoint validation, got: {entrypoint_errors}"


# ---------------------------------------------------------------------------
# 8. Server-side registry re-verification (the trust gate)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_forged_owner_overridden_by_server(client, session):
    """Client report claims owner_verified=true, but the registry repo points
    elsewhere. Server marks mismatch; submission lands action_required."""
    token, _ = await setup_publisher_user(client, "ev@test.dev", "evpub", "TestPass123!", "pub-ev", "Ev")
    manifest = _mcp_manifest("evil-mcp", ["npx", "-y", "evil-mcp@1.0.0"],
                             "https://github.com/attacker/evil-mcp", "mcp-evil")
    resp = await client.post("/v1/mcp/submit",
                             json={"manifest": manifest, "verification_report": TESTED_REPORT},
                             headers=_auth(token))
    assert resp.status_code == 201
    assert resp.json()["status"] == "action_required"  # forged green report overridden

    sid = resp.json()["id"]
    sv = (await client.get(f"/v1/mcp/submissions/{sid}", headers=_auth(token))).json()["server_verification"]
    assert sv["server_status"] == "mismatch"
    assert sv["repo_consistency"] == "mismatch"


@pytest.mark.asyncio
async def test_fake_package_overridden_by_server(client, session):
    """Client claims package_exists=true, but npm returns 404 -> mismatch."""
    token, _ = await setup_publisher_user(client, "gh@test.dev", "ghpub", "TestPass123!", "pub-gh", "Gh")
    manifest = _mcp_manifest("ghost-mcp", ["npx", "-y", "ghost-mcp@1.0.0"],
                             "https://github.com/test/ghost-mcp", "mcp-ghost")
    resp = await client.post("/v1/mcp/submit",
                             json={"manifest": manifest, "verification_report": TESTED_REPORT},
                             headers=_auth(token))
    assert resp.status_code == 201
    assert resp.json()["status"] == "action_required"

    sid = resp.json()["id"]
    sv = (await client.get(f"/v1/mcp/submissions/{sid}", headers=_auth(token))).json()["server_verification"]
    assert sv["server_status"] == "mismatch"
    assert sv["package_exists"] is False


@pytest.mark.asyncio
async def test_fake_pinned_version_overridden_by_server(client, session):
    """Command pins a version that is not published -> mismatch."""
    token, _ = await setup_publisher_user(client, "fv@test.dev", "fvpub", "TestPass123!", "pub-fv", "Fv")
    manifest = _mcp_manifest("test-mcp", ["npx", "-y", "test-mcp@9.9.9"],
                             "https://github.com/test/test-mcp", "mcp-fv")
    resp = await client.post("/v1/mcp/submit",
                             json={"manifest": manifest, "verification_report": TESTED_REPORT},
                             headers=_auth(token))
    assert resp.status_code == 201
    assert resp.json()["status"] == "action_required"

    sid = resp.json()["id"]
    sv = (await client.get(f"/v1/mcp/submissions/{sid}", headers=_auth(token))).json()["server_verification"]
    assert sv["server_status"] == "mismatch"
    assert sv["package_exists"] is True
    assert sv["version_exists"] is False


@pytest.mark.asyncio
async def test_unpinned_command_resolves_version(client, session):
    """Unpinned command is a warning, not a block: server resolves latest."""
    token, _ = await setup_publisher_user(client, "up@test.dev", "uppub", "TestPass123!", "pub-up", "Up")
    manifest = _mcp_manifest("test-mcp", ["npx", "-y", "test-mcp"],
                             "https://github.com/test/test-mcp", "mcp-up")
    resp = await client.post("/v1/mcp/submit",
                             json={"manifest": manifest, "verification_report": TESTED_REPORT},
                             headers=_auth(token))
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"

    sid = resp.json()["id"]
    sv = (await client.get(f"/v1/mcp/submissions/{sid}", headers=_auth(token))).json()["server_verification"]
    assert sv["server_status"] == "verified"
    assert sv["resolved_version"] == "1.0.0"
    assert sv["command_pinning"] == "unpinned_resolved"


@pytest.mark.asyncio
async def test_registry_repo_missing_is_indeterminate(client, session):
    """Registry has no repo URL: indeterminate, NOT blocked."""
    token, _ = await setup_publisher_user(client, "nr@test.dev", "nrpub", "TestPass123!", "pub-nr", "Nr")
    manifest = _mcp_manifest("norepo-mcp", ["npx", "-y", "norepo-mcp@2.0.0"],
                             "https://github.com/whoever/norepo-mcp", "mcp-norepo")
    resp = await client.post("/v1/mcp/submit",
                             json={"manifest": manifest, "verification_report": TESTED_REPORT},
                             headers=_auth(token))
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"

    sid = resp.json()["id"]
    sv = (await client.get(f"/v1/mcp/submissions/{sid}", headers=_auth(token))).json()["server_verification"]
    assert sv["server_status"] == "indeterminate"
    assert sv["repo_consistency"] == "indeterminate"


@pytest.mark.asyncio
async def test_registry_unavailable_is_not_passed(client, session, monkeypatch):
    """Registry down -> unavailable (never silently 'passed'), submit still succeeds."""
    async def boom(name):
        raise RegistryUnavailable("registry down")
    monkeypatch.setattr("app.mcp.registry_verify._fetch_npm", boom)

    token, _ = await setup_publisher_user(client, "un@test.dev", "unpub", "TestPass123!", "pub-un", "Un")
    resp = await client.post("/v1/mcp/submit",
                             json={"manifest": MCP_MANIFEST, "verification_report": TESTED_REPORT},
                             headers=_auth(token))
    assert resp.status_code == 201  # not a 500
    sid = resp.json()["id"]
    sv = (await client.get(f"/v1/mcp/submissions/{sid}", headers=_auth(token))).json()["server_verification"]
    assert sv["server_status"] == "unavailable"


@pytest.mark.asyncio
async def test_publish_gate_uses_server_not_client_report(client, session):
    """The headline guarantee: a fully-green client report cannot publish if the
    server says the package does not exist."""
    token, _ = await setup_publisher_user(client, "pg@test.dev", "pgpub", "TestPass123!", "pub-pg", "Pg")
    manifest = _mcp_manifest("ghost-mcp", ["npx", "-y", "ghost-mcp@1.0.0"],
                             "https://github.com/test/ghost-mcp", "mcp-ghost-pub")
    submit_resp = await client.post("/v1/mcp/submit",
                                    json={"manifest": manifest, "verification_report": TESTED_REPORT},
                                    headers=_auth(token))
    sub_id = submit_resp.json()["id"]

    admin_token = await _make_admin(client, session, "adminpg@test.dev", "adminpg")
    # Force-approve despite server mismatch (admin override of status)
    await client.put(f"/v1/admin/mcp/submissions/{sub_id}/review",
                     json={"status": "approved"}, headers=_auth(admin_token))

    resp = await client.post(f"/v1/admin/mcp/submissions/{sub_id}/publish", headers=_auth(admin_token))
    assert resp.status_code == 400
    msg = resp.json()["error"]["message"]
    assert "mismatch" in msg or "package exists" in msg


@pytest.mark.asyncio
async def test_reverify_recovers_unavailable(client, session, monkeypatch):
    """A submission stuck on 'unavailable' recovers via the re-verify endpoint."""
    async def boom(name):
        raise RegistryUnavailable("registry down")
    monkeypatch.setattr("app.mcp.registry_verify._fetch_npm", boom)

    token, _ = await setup_publisher_user(client, "rv@test.dev", "rvpub", "TestPass123!", "pub-rv", "Rv")
    submit_resp = await client.post("/v1/mcp/submit",
                                    json={"manifest": MCP_MANIFEST, "verification_report": TESTED_REPORT},
                                    headers=_auth(token))
    sub_id = submit_resp.json()["id"]
    sv = (await client.get(f"/v1/mcp/submissions/{sub_id}", headers=_auth(token))).json()["server_verification"]
    assert sv["server_status"] == "unavailable"

    # Registry comes back; admin re-verifies
    async def ok(name):
        return _FAKE_NPM.get(name)
    monkeypatch.setattr("app.mcp.registry_verify._fetch_npm", ok)

    admin_token = await _make_admin(client, session, "adminrv@test.dev", "adminrv")
    resp = await client.post(f"/v1/admin/mcp/submissions/{sub_id}/reverify", headers=_auth(admin_token))
    assert resp.status_code == 200
    assert resp.json()["server_status"] == "verified"


@pytest.mark.asyncio
async def test_registry_allowlist_blocks_foreign_host():
    """Defense-in-depth: the registry client refuses any host outside the allowlist."""
    from app.mcp.registry_verify import _http_get_json
    with pytest.raises(RegistryUnavailable):
        await _http_get_json("https://evil.example.com/registry/foo")
