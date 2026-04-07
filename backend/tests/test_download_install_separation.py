"""Tests for download/install count separation.

Test 1: /download bumps only download_count, not install_count
Test 2: /install bumps only install_count, not download_count
Test 3: Redis dedup works independently for both counters
Test 4: Dedup is version-specific (same user, different versions → both count)
Test 5: Backfill correctness — status whitelist (installed + active, not failed)
"""
import json
from unittest.mock import patch

import pytest
from sqlalchemy import select, update

from app.packages.models import Installation, Package
from app.publishers.models import Publisher

TEST_USER = {
    "email": "countsep@agentnode.dev",
    "username": "countsep",
    "password": "TestPass123!",
}

TEST_PUBLISHER = {
    "display_name": "Count Sep Publisher",
    "slug": "count-sep-pub",
}

TEST_MANIFEST = {
    "manifest_version": "0.1",
    "package_id": "count-sep-pkg",
    "package_type": "toolpack",
    "name": "Count Separation Package",
    "publisher": "count-sep-pub",
    "version": "1.0.0",
    "summary": "A package for count separation testing.",
    "runtime": "python",
    "install_mode": "package",
    "hosting_type": "agentnode_hosted",
    "entrypoint": "count_sep.tool",
    "capabilities": {
        "tools": [{
            "name": "test_tool",
            "capability_id": "pdf_extraction",
            "description": "Test tool",
            "input_schema": {"type": "object", "properties": {"input": {"type": "string"}}},
        }],
        "resources": [],
        "prompts": [],
    },
    "compatibility": {"frameworks": ["generic"], "python": ">=3.10"},
    "permissions": {
        "network": {"level": "none", "allowed_domains": []},
        "filesystem": {"level": "temp"},
        "code_execution": {"level": "none"},
        "data_access": {"level": "input_only"},
        "user_approval": {"required": "never"},
        "external_integrations": [],
    },
    "tags": ["test"],
    "categories": ["document-processing"],
    "dependencies": [],
    "security": {"signature": "", "provenance": {"source_repo": "", "commit": "", "build_system": ""}},
}


async def setup(client, session):
    """Register user, login, create trusted publisher (bypasses quarantine). Returns token."""
    await client.post("/v1/auth/register", json=TEST_USER)
    login = await client.post("/v1/auth/login", json={
        "email": TEST_USER["email"],
        "password": TEST_USER["password"],
    })
    token = login.json()["access_token"]
    await client.post(
        "/v1/publishers",
        json=TEST_PUBLISHER,
        headers={"Authorization": f"Bearer {token}"},
    )
    # Make publisher trusted to bypass new-publisher quarantine
    await session.execute(
        update(Publisher)
        .where(Publisher.slug == TEST_PUBLISHER["slug"])
        .values(trust_level="trusted")
    )
    await session.commit()
    return token


async def publish(client, token, manifest=None):
    m = manifest or TEST_MANIFEST
    return await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(m)},
        headers={"Authorization": f"Bearer {token}"},
    )


async def get_counts(client, slug="count-sep-pkg"):
    resp = await client.get(f"/v1/packages/{slug}")
    data = resp.json()
    return data.get("download_count", 0), data.get("install_count", 0)


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_download_bumps_only_download_count(mock_meili, mock_s3, client, session):
    """Test 1: /download bumps only download_count, not install_count."""
    token = await setup(client, session)
    await publish(client, token)

    resp = await client.post("/v1/packages/count-sep-pkg/download")
    assert resp.status_code == 200
    assert resp.json()["download_count"] == 1

    dl, inst = await get_counts(client)
    assert dl == 1
    assert inst == 0


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_install_bumps_only_install_count(mock_meili, mock_s3, client, session):
    """Test 2: /install bumps only install_count, not download_count."""
    token = await setup(client, session)
    await publish(client, token)

    resp = await client.post(
        "/v1/packages/count-sep-pkg/install",
        json={"source": "cli", "event_type": "install"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    dl, inst = await get_counts(client)
    assert dl == 0
    assert inst == 1


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_redis_dedup_independent(mock_meili, mock_s3, client, session):
    """Test 3: Redis dedup works for both counters independently."""
    token = await setup(client, session)
    await publish(client, token)

    # Two downloads from same IP — deduped to 1
    await client.post("/v1/packages/count-sep-pkg/download")
    await client.post("/v1/packages/count-sep-pkg/download")

    # Two installs from same user — deduped to 1
    await client.post(
        "/v1/packages/count-sep-pkg/install",
        json={"source": "cli", "event_type": "install"},
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.post(
        "/v1/packages/count-sep-pkg/install",
        json={"source": "cli", "event_type": "install"},
        headers={"Authorization": f"Bearer {token}"},
    )

    dl, inst = await get_counts(client)
    assert dl == 1
    assert inst == 1


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_dedup_version_specific(mock_meili, mock_s3, client, session):
    """Test 4: Dedup is version-specific — same user, different versions → both count."""
    token = await setup(client, session)
    await publish(client, token)
    v2 = {**TEST_MANIFEST, "version": "2.0.0"}
    await publish(client, token, manifest=v2)

    # Install v1.0.0
    await client.post(
        "/v1/packages/count-sep-pkg/install",
        json={"source": "cli", "event_type": "install", "version": "1.0.0"},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Install v2.0.0 — different version, should count separately
    await client.post(
        "/v1/packages/count-sep-pkg/install",
        json={"source": "cli", "event_type": "install", "version": "2.0.0"},
        headers={"Authorization": f"Bearer {token}"},
    )

    dl, inst = await get_counts(client)
    assert dl == 0
    assert inst == 2


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_backfill_correctness(mock_meili, mock_s3, client, session):
    """Test 5: Backfill only counts installed + active, not failed."""
    token = await setup(client, session)
    await publish(client, token)

    # Retrieve package and version IDs
    from app.packages.models import PackageVersion
    result = await session.execute(
        select(Package).where(Package.slug == "count-sep-pkg")
    )
    pkg = result.scalar_one()
    pv_result = await session.execute(
        select(PackageVersion).where(PackageVersion.package_id == pkg.id).limit(1)
    )
    pv = pv_result.scalar_one()

    # Get user ID
    from app.auth.models import User
    user_result = await session.execute(
        select(User).where(User.email == TEST_USER["email"])
    )
    user = user_result.scalar_one()

    # Create 5 installation records manually:
    # 2 installed, 2 active, 1 failed → count should be 4
    for status in ["installed", "installed", "active", "active", "failed"]:
        inst = Installation(
            user_id=user.id,
            package_id=pkg.id,
            package_version_id=pv.id,
            source="cli",
            status=status,
            event_type="install",
            installation_context={},
        )
        session.add(inst)
    await session.flush()

    # Reset install_count to 0 to simulate pre-migration state
    pkg.install_count = 0
    await session.flush()

    # Simulate backfill query (same as migration 025)
    from sqlalchemy import func, update
    subq = (
        select(
            Installation.package_id,
            func.count(Installation.id).label("cnt"),
        )
        .where(Installation.status.in_(("installed", "active")))
        .group_by(Installation.package_id)
        .subquery()
    )
    await session.execute(
        update(Package)
        .where(Package.id == subq.c.package_id)
        .values(install_count=subq.c.cnt)
    )
    await session.flush()

    await session.refresh(pkg)
    assert pkg.install_count == 4  # 2 installed + 2 active, not the failed one
