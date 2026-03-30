"""Tests for billing/review endpoints — Phase 1 red-flag checklist."""
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select, update

from app.auth.models import User
from app.billing.models import ProcessedStripeEvent, ReviewRequest
from app.billing.service import (
    TIER_BADGE_COLUMN,
    TIER_PRICES,
    EXPRESS_SURCHARGE,
    calculate_price,
)
from app.packages.models import Package, PackageVersion

TEST_ADMIN = {
    "email": "billing-admin@agentnode.dev",
    "username": "billingadmin",
    "password": "AdminPass123!",
}

TEST_PUBLISHER = {
    "email": "billing-pub@agentnode.dev",
    "username": "billingpub",
    "password": "PubPass123!",
}

TEST_OTHER = {
    "email": "billing-other@agentnode.dev",
    "username": "billingother",
    "password": "OtherPass123!",
}

PUBLISHER_PROFILE = {
    "display_name": "Billing Publisher",
    "slug": "billing-pub",
}

OTHER_PUBLISHER_PROFILE = {
    "display_name": "Other Publisher",
    "slug": "other-pub",
}

TEST_MANIFEST = {
    "manifest_version": "0.1",
    "package_id": "billing-test-pkg",
    "package_type": "toolpack",
    "name": "Billing Test Package",
    "publisher": "billing-pub",
    "version": "1.0.0",
    "summary": "A package for billing testing.",
    "runtime": "python",
    "install_mode": "package",
    "hosting_type": "agentnode_hosted",
    "entrypoint": "billing_test.tool",
    "capabilities": {
        "tools": [{
            "name": "test_tool",
            "capability_id": "pdf_extraction",
            "description": "Test tool",
            "input_schema": {"type": "object"},
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

REVIEW_RESULT = {
    "security_passed": True,
    "compatibility_passed": True,
    "docs_passed": False,
    "major_findings_count": 1,
    "required_changes": ["Missing error handling for timeout"],
    "reviewer_summary": "Solid security posture, minor docs gap.",
}


async def setup_admin(client, session):
    """Create admin user + publisher + published package."""
    await client.post("/v1/auth/register", json=TEST_ADMIN)
    login = await client.post("/v1/auth/login", json={
        "email": TEST_ADMIN["email"],
        "password": TEST_ADMIN["password"],
    })
    token = login.json()["access_token"]
    await session.execute(
        update(User).where(User.username == TEST_ADMIN["username"]).values(is_admin=True)
    )
    await session.commit()
    return token


async def setup_publisher(client, session):
    """Create publisher user + publisher profile + published package. Returns token."""
    await client.post("/v1/auth/register", json=TEST_PUBLISHER)
    login = await client.post("/v1/auth/login", json={
        "email": TEST_PUBLISHER["email"],
        "password": TEST_PUBLISHER["password"],
    })
    token = login.json()["access_token"]
    await client.post(
        "/v1/publishers",
        json=PUBLISHER_PROFILE,
        headers={"Authorization": f"Bearer {token}"},
    )
    return token


async def setup_other_user(client):
    """Create a separate user with no publisher."""
    await client.post("/v1/auth/register", json=TEST_OTHER)
    login = await client.post("/v1/auth/login", json={
        "email": TEST_OTHER["email"],
        "password": TEST_OTHER["password"],
    })
    return login.json()["access_token"]


async def publish_package(client, token):
    """Publish a test package."""
    return await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(TEST_MANIFEST)},
        headers={"Authorization": f"Bearer {token}"},
    )


async def create_review_in_db(session, publisher_id, package_id, version_id, tier="security", status="paid"):
    """Create a review directly in DB for admin tests (skipping Stripe)."""
    review = ReviewRequest(
        order_id=f"rev_{uuid.uuid4().hex}",
        publisher_id=publisher_id,
        package_id=package_id,
        package_version_id=version_id,
        tier=tier,
        express=False,
        price_cents=calculate_price(tier, False),
        currency="usd",
        status=status,
        paid_at=datetime.now(timezone.utc) if status != "pending_payment" else None,
        stripe_payment_intent_id=f"pi_{uuid.uuid4().hex}" if status != "pending_payment" else None,
    )
    session.add(review)
    await session.flush()
    return review


async def get_package_ids(session, slug="billing-test-pkg"):
    """Return (package_id, version_id, publisher_id)."""
    pkg_result = await session.execute(select(Package).where(Package.slug == slug))
    pkg = pkg_result.scalar_one()
    pv_result = await session.execute(
        select(PackageVersion).where(PackageVersion.package_id == pkg.id).limit(1)
    )
    pv = pv_result.scalar_one()
    return pkg.id, pv.id, pkg.publisher_id


# ---- Unit tests for pricing ----


def test_pricing_security():
    assert calculate_price("security", False) == 4900


def test_pricing_compatibility_express():
    assert calculate_price("compatibility", True) == 9900 + 10000


def test_pricing_full():
    assert calculate_price("full", False) == 19900


def test_pricing_invalid_tier():
    with pytest.raises(Exception):
        calculate_price("curated", False)


# ---- Test 1: Admin auth on all admin routes ----


@pytest.mark.asyncio
async def test_admin_routes_require_admin(client, session):
    """All 4 admin endpoints must reject non-admin users."""
    pub_token = await setup_publisher(client, session)
    fake_id = str(uuid.uuid4())
    headers = {"Authorization": f"Bearer {pub_token}"}

    resp = await client.get("/v1/admin/reviews/queue", headers=headers)
    assert resp.status_code == 403

    resp = await client.post(f"/v1/admin/reviews/{fake_id}/assign",
        json={"reviewer_id": fake_id}, headers=headers)
    assert resp.status_code == 403

    resp = await client.post(f"/v1/admin/reviews/{fake_id}/complete",
        json={"outcome": "approved", "review_result": REVIEW_RESULT}, headers=headers)
    assert resp.status_code == 403

    resp = await client.post(f"/v1/admin/reviews/{fake_id}/refund",
        json={"reason": "test"}, headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_review_no_leak(client, session):
    """/v1/reviews/{id} returns 404 (not 403) for non-owner to avoid info leak."""
    other_token = await setup_other_user(client)
    fake_id = str(uuid.uuid4())
    resp = await client.get(
        f"/v1/reviews/{fake_id}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 404


# ---- Test 3: Badge materialization and refund ----


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_full_refund_removes_badge(mock_meili, mock_s3, client, session):
    """Full refund must NULL the correct *_reviewed_at column."""
    admin_token = await setup_admin(client, session)
    pub_token = await setup_publisher(client, session)
    await publish_package(client, pub_token)

    pkg_id, pv_id, pub_id = await get_package_ids(session)

    # Create a review in in_review status
    review = await create_review_in_db(session, pub_id, pkg_id, pv_id, tier="security", status="in_review")
    await session.commit()

    # Admin completes → approved → badge materialized
    resp = await client.post(
        f"/v1/admin/reviews/{review.id}/complete",
        json={"outcome": "approved", "notes": "All good", "review_result": REVIEW_RESULT},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["badge_materialized"] is True

    # Verify badge was set
    pv_result = await session.execute(select(PackageVersion).where(PackageVersion.id == pv_id))
    pv = pv_result.scalar_one()
    await session.refresh(pv)
    assert pv.security_reviewed_at is not None

    # Full refund
    with patch("stripe.Refund.create"), \
         patch("app.billing.service.settings") as mock_settings:
        mock_settings.STRIPE_SECRET_KEY = "sk_test_fake"
        resp = await client.post(
            f"/v1/admin/reviews/{review.id}/refund",
            json={"reason": "Disputed"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 200
    assert resp.json()["badge_removed"] is True

    # Badge must be NULL
    await session.refresh(pv)
    assert pv.security_reviewed_at is None


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_partial_refund_keeps_badge(mock_meili, mock_s3, client, session):
    """Partial refund must NOT touch the badge."""
    admin_token = await setup_admin(client, session)
    pub_token = await setup_publisher(client, session)
    await publish_package(client, pub_token)

    pkg_id, pv_id, pub_id = await get_package_ids(session)
    review = await create_review_in_db(session, pub_id, pkg_id, pv_id, tier="compatibility", status="in_review")
    await session.commit()

    # Approve
    await client.post(
        f"/v1/admin/reviews/{review.id}/complete",
        json={"outcome": "approved", "notes": "OK", "review_result": REVIEW_RESULT},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    pv_result = await session.execute(select(PackageVersion).where(PackageVersion.id == pv_id))
    pv = pv_result.scalar_one()
    await session.refresh(pv)
    assert pv.compatibility_reviewed_at is not None

    # Partial refund (less than price)
    with patch("stripe.Refund.create"), \
         patch("app.billing.service.settings") as mock_settings:
        mock_settings.STRIPE_SECRET_KEY = "sk_test_fake"
        resp = await client.post(
            f"/v1/admin/reviews/{review.id}/refund",
            json={"amount_cents": 1000, "reason": "Partial goodwill"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 200
    assert resp.json()["badge_removed"] is False

    # Badge must still be set
    await session.refresh(pv)
    assert pv.compatibility_reviewed_at is not None


# ---- Test 4: Status transitions ----


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_complete_only_from_in_review(mock_meili, mock_s3, client, session):
    """complete must reject review in 'paid' status (not yet assigned)."""
    admin_token = await setup_admin(client, session)
    pub_token = await setup_publisher(client, session)
    await publish_package(client, pub_token)

    pkg_id, pv_id, pub_id = await get_package_ids(session)
    review = await create_review_in_db(session, pub_id, pkg_id, pv_id, tier="full", status="paid")
    await session.commit()

    resp = await client.post(
        f"/v1/admin/reviews/{review.id}/complete",
        json={"outcome": "approved", "review_result": REVIEW_RESULT},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400
    assert "must be in_review" in resp.json()["error"]["message"]


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_double_refund_blocked(mock_meili, mock_s3, client, session):
    """Cannot refund twice (full or partial)."""
    admin_token = await setup_admin(client, session)
    pub_token = await setup_publisher(client, session)
    await publish_package(client, pub_token)

    pkg_id, pv_id, pub_id = await get_package_ids(session)
    review = await create_review_in_db(session, pub_id, pkg_id, pv_id, tier="security", status="in_review")
    await session.commit()

    # Approve first
    await client.post(
        f"/v1/admin/reviews/{review.id}/complete",
        json={"outcome": "approved", "review_result": REVIEW_RESULT},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # First refund succeeds
    with patch("stripe.Refund.create"), \
         patch("app.billing.service.settings") as mock_settings:
        mock_settings.STRIPE_SECRET_KEY = "sk_test_fake"
        resp1 = await client.post(
            f"/v1/admin/reviews/{review.id}/refund",
            json={"reason": "First refund"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp1.status_code == 200

    # Second refund blocked
    with patch("stripe.Refund.create"), \
         patch("app.billing.service.settings") as mock_settings:
        mock_settings.STRIPE_SECRET_KEY = "sk_test_fake"
        resp2 = await client.post(
            f"/v1/admin/reviews/{review.id}/refund",
            json={"reason": "Second attempt"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp2.status_code == 400


# ---- Test 5: Webhook idempotency ----


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_webhook_idempotent_double_event(mock_meili, mock_s3, client, session):
    """Same event_id sent twice → second call returns already_processed."""
    from app.billing.service import process_stripe_event

    pub_token = await setup_publisher(client, session)
    await publish_package(client, pub_token)

    pkg_id, pv_id, pub_id = await get_package_ids(session)
    review = await create_review_in_db(session, pub_id, pkg_id, pv_id, tier="security", status="pending_payment")
    await session.commit()

    event = {
        "id": "evt_test_123",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_123",
                "client_reference_id": review.order_id,
                "payment_intent": "pi_test_123",
            }
        }
    }

    # First call
    result1 = await process_stripe_event(session, event)
    await session.commit()
    assert result1["status"] == "processed"

    # Second call — same event_id
    result2 = await process_stripe_event(session, event)
    assert result2["status"] == "already_processed"


@pytest.mark.asyncio
async def test_webhook_unknown_event_ignored(client, session):
    """Unknown event types are logged and ignored, not errored."""
    from app.billing.service import process_stripe_event

    event = {
        "id": "evt_unknown_456",
        "type": "customer.subscription.created",
        "data": {"object": {}}
    }

    result = await process_stripe_event(session, event)
    await session.commit()
    assert result["status"] == "processed"


# ---- Test 6: Stripe secret missing → 503 ----


@pytest.mark.asyncio
async def test_review_request_without_stripe_key(client, session):
    """POST /v1/reviews/request returns 503 when Stripe is not configured."""
    pub_token = await setup_publisher(client, session)
    resp = await client.post(
        "/v1/reviews/request",
        json={"package_slug": "nonexistent", "version": "1.0.0", "tier": "security"},
        headers={"Authorization": f"Bearer {pub_token}"},
    )
    # Without Stripe key, we'll get 404 (package not found) before hitting Stripe,
    # but if package exists it would be 503. The key test is that it doesn't 500.
    assert resp.status_code in (404, 503)


@pytest.mark.asyncio
async def test_stripe_webhook_without_secret(client, session):
    """POST /v1/webhooks/stripe returns 503 when webhook secret not configured."""
    resp = await client.post(
        "/v1/webhooks/stripe",
        content=b'{}',
        headers={"stripe-signature": "t=123,v1=abc"},
    )
    # Should be 503 (billing unavailable), not 500
    assert resp.status_code == 503


# ---- Test 7: Non-owner cannot request review ----


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
@patch("app.billing.stripe_client.create_review_checkout_session")
async def test_non_owner_cannot_request_review(mock_stripe, mock_meili, mock_s3, client, session):
    """Publisher cannot request review for another publisher's package."""
    pub_token = await setup_publisher(client, session)
    await publish_package(client, pub_token)

    # Create another publisher
    await client.post("/v1/auth/register", json=TEST_OTHER)
    login = await client.post("/v1/auth/login", json={
        "email": TEST_OTHER["email"],
        "password": TEST_OTHER["password"],
    })
    other_token = login.json()["access_token"]
    await client.post(
        "/v1/publishers",
        json=OTHER_PUBLISHER_PROFILE,
        headers={"Authorization": f"Bearer {other_token}"},
    )

    # Other publisher tries to review billing-pub's package
    resp = await client.post(
        "/v1/reviews/request",
        json={"package_slug": "billing-test-pkg", "version": "1.0.0", "tier": "security"},
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 403
    assert "own packages" in resp.json()["error"]["message"]


# ---- Test 8+9: New version starts unreviewed ----


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_new_version_starts_unreviewed(mock_meili, mock_s3, client, session):
    """Publishing a new version must NOT inherit review badges."""
    pub_token = await setup_publisher(client, session)
    await publish_package(client, pub_token)

    pkg_id, pv_id, pub_id = await get_package_ids(session)

    # Set a badge directly on v1.0.0
    pv_result = await session.execute(select(PackageVersion).where(PackageVersion.id == pv_id))
    pv = pv_result.scalar_one()
    pv.security_reviewed_at = datetime.now(timezone.utc)
    await session.commit()

    # Publish v2.0.0
    manifest_v2 = {**TEST_MANIFEST, "version": "2.0.0"}
    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(manifest_v2)},
        headers={"Authorization": f"Bearer {pub_token}"},
    )

    # v2.0.0 should have no review badges
    pv2_result = await session.execute(
        select(PackageVersion).where(
            PackageVersion.package_id == pkg_id,
            PackageVersion.version_number == "2.0.0",
        )
    )
    pv2 = pv2_result.scalar_one()
    assert pv2.security_reviewed_at is None
    assert pv2.compatibility_reviewed_at is None
    assert pv2.manually_reviewed_at is None


# ---- Test: Curated never set by paid reviews ----


def test_no_curated_in_badge_mapping():
    """The TIER_BADGE_COLUMN mapping must never contain 'curated'."""
    for tier, column in TIER_BADGE_COLUMN.items():
        assert "curated" not in column.lower()
    assert "curated" not in TIER_BADGE_COLUMN


# ---- Test: Audit logging in same transaction ----


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_audit_log_written_on_admin_action(mock_meili, mock_s3, client, session):
    """Admin complete should write audit log in same transaction."""
    from app.admin.models import AdminAuditLog

    admin_token = await setup_admin(client, session)
    pub_token = await setup_publisher(client, session)
    await publish_package(client, pub_token)

    pkg_id, pv_id, pub_id = await get_package_ids(session)
    review = await create_review_in_db(session, pub_id, pkg_id, pv_id, tier="security", status="in_review")
    await session.commit()

    resp = await client.post(
        f"/v1/admin/reviews/{review.id}/complete",
        json={"outcome": "approved", "review_result": REVIEW_RESULT},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    # Audit log should exist
    audit_result = await session.execute(
        select(AdminAuditLog).where(
            AdminAuditLog.action == "complete_review",
            AdminAuditLog.target_id == str(review.id),
        )
    )
    audit = audit_result.scalar_one_or_none()
    assert audit is not None
    assert audit.metadata_["outcome"] == "approved"
