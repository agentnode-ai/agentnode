"""Integration tests for the media library API (images endpoints)."""
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import update

from app.auth.models import User
from app.blog.models import BlogImage, BlogPost, BlogPostType


# ── Helpers ──

TEST_ADMIN = {
    "email": "media-admin@agentnode.dev",
    "username": "mediaadmin",
    "password": "AdminPass123!",
}


async def _setup_admin(client, session) -> str:
    """Register, login, promote to admin. Returns JWT token."""
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


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_post_type(session) -> BlogPostType:
    """Seed the default 'post' type required by BlogPost FK."""
    pt = BlogPostType(name="Post", slug="post", url_prefix="blog", icon="article")
    session.add(pt)
    await session.commit()
    await session.refresh(pt)
    return pt


async def _create_image(
    session,
    *,
    post_id=None,
    alt_text=None,
    title=None,
    original_filename=None,
    caption=None,
    file_size=1000,
    created_at=None,
) -> BlogImage:
    """Insert a BlogImage directly in the DB."""
    img = BlogImage(
        id=uuid.uuid4(),
        post_id=post_id,
        object_key=f"blog/{uuid.uuid4()}.jpg",
        url=f"https://cdn.test/{uuid.uuid4()}.jpg",
        alt_text=alt_text,
        title=title,
        original_filename=original_filename,
        caption=caption,
        file_size=file_size,
        width=800,
        height=600,
    )
    session.add(img)
    await session.commit()
    await session.refresh(img)
    # Override created_at if needed (server_default makes it hard to set via constructor)
    if created_at:
        img.created_at = created_at
        await session.commit()
        await session.refresh(img)
    return img


async def _create_post(session, author_id, post_type_id, slug_suffix="") -> BlogPost:
    """Create a minimal BlogPost for attachment tests."""
    post = BlogPost(
        title=f"Test Post {slug_suffix}",
        slug=f"test-post-{slug_suffix or uuid.uuid4().hex[:8]}",
        author_id=author_id,
        post_type_id=post_type_id,
        status="draft",
    )
    session.add(post)
    await session.commit()
    await session.refresh(post)
    return post


# ── Month Filter Tests ──

@pytest.mark.asyncio
class TestMonthFilter:
    async def test_valid_month_returns_200(self, client, session):
        token = await _setup_admin(client, session)
        await _create_image(session)

        resp = await client.get(
            "/v1/admin/blog/images?month=2026-03",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "images" in data
        assert "total" in data

    @pytest.mark.parametrize("bad_month", [
        "2026-13",       # month > 12
        "2026-00",       # month 0
        "1999-12",       # year < 2000
        "2101-01",       # year > 2100
        "abcd-ef",       # non-numeric
        "2026/03",       # wrong separator
        "03-2026",       # reversed
    ])
    async def test_invalid_month_returns_422(self, client, session, bad_month):
        token = await _setup_admin(client, session)

        resp = await client.get(
            f"/v1/admin/blog/images?month={bad_month}",
            headers=_auth(token),
        )
        assert resp.status_code == 422

    async def test_month_filter_only_returns_matching_images(self, client, session):
        token = await _setup_admin(client, session)

        march = await _create_image(session, alt_text="march-pic")
        march.created_at = datetime(2026, 3, 15, tzinfo=timezone.utc)
        await session.commit()

        april = await _create_image(session, alt_text="april-pic")
        april.created_at = datetime(2026, 4, 10, tzinfo=timezone.utc)
        await session.commit()

        resp = await client.get(
            "/v1/admin/blog/images?month=2026-03",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        data = resp.json()
        ids = [img["id"] for img in data["images"]]
        assert str(march.id) in ids
        assert str(april.id) not in ids


# ── Months Endpoint Tests ──

@pytest.mark.asyncio
class TestMonthsEndpoint:
    async def test_months_returns_list(self, client, session):
        token = await _setup_admin(client, session)
        await _create_image(session)

        resp = await client.get(
            "/v1/admin/blog/images/months",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        if len(data) > 0:
            assert "value" in data[0]
            assert "label" in data[0]

    async def test_months_empty_when_no_images(self, client, session):
        token = await _setup_admin(client, session)

        resp = await client.get(
            "/v1/admin/blog/images/months",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.json() == []


# ── Bulk Delete Tests ──

@pytest.mark.asyncio
class TestBulkDelete:
    @patch("app.blog.router.delete_artifact")
    async def test_deletes_unattached_images(self, mock_s3, client, session):
        token = await _setup_admin(client, session)
        img1 = await _create_image(session, alt_text="free1")
        img2 = await _create_image(session, alt_text="free2")

        resp = await client.post(
            "/v1/admin/blog/images/bulk-delete",
            json={"ids": [str(img1.id), str(img2.id)]},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["deleted"] == 2
        assert body["skipped_attached"] == 0
        assert body["not_found"] == 0

    @patch("app.blog.router.delete_artifact")
    async def test_skips_attached_images(self, mock_s3, client, session):
        token = await _setup_admin(client, session)
        pt = await _seed_post_type(session)

        # Get admin user id for post author
        user_result = await session.execute(
            User.__table__.select().where(User.username == TEST_ADMIN["username"])
        )
        admin_user = user_result.first()
        post = await _create_post(session, admin_user.id, pt.id, slug_suffix="attached")

        img = await _create_image(session, post_id=post.id, alt_text="attached")

        resp = await client.post(
            "/v1/admin/blog/images/bulk-delete",
            json={"ids": [str(img.id)]},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["deleted"] == 0
        assert body["skipped_attached"] == 1

    @patch("app.blog.router.delete_artifact")
    async def test_mixed_ids(self, mock_s3, client, session):
        token = await _setup_admin(client, session)
        pt = await _seed_post_type(session)

        user_result = await session.execute(
            User.__table__.select().where(User.username == TEST_ADMIN["username"])
        )
        admin_user = user_result.first()
        post = await _create_post(session, admin_user.id, pt.id, slug_suffix="mixed")

        free_img = await _create_image(session, alt_text="free")
        attached_img = await _create_image(session, post_id=post.id, alt_text="attached")
        missing_id = str(uuid.uuid4())

        resp = await client.post(
            "/v1/admin/blog/images/bulk-delete",
            json={"ids": [str(free_img.id), str(attached_img.id), missing_id]},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["deleted"] == 1
        assert body["skipped_attached"] == 1
        assert body["not_found"] == 1

    async def test_rejects_empty_id_list(self, client, session):
        token = await _setup_admin(client, session)

        resp = await client.post(
            "/v1/admin/blog/images/bulk-delete",
            json={"ids": []},
            headers=_auth(token),
        )
        assert resp.status_code == 422

    async def test_rejects_more_than_50_ids(self, client, session):
        token = await _setup_admin(client, session)

        resp = await client.post(
            "/v1/admin/blog/images/bulk-delete",
            json={"ids": [str(uuid.uuid4()) for _ in range(51)]},
            headers=_auth(token),
        )
        assert resp.status_code == 422


# ── Search Tests ──

@pytest.mark.asyncio
class TestSearch:
    async def test_search_finds_by_title(self, client, session):
        token = await _setup_admin(client, session)
        img = await _create_image(session, title="Sunset Banner", original_filename="a.jpg")

        resp = await client.get(
            "/v1/admin/blog/images?search=Sunset",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        ids = [i["id"] for i in resp.json()["images"]]
        assert str(img.id) in ids

    async def test_search_finds_by_alt_text(self, client, session):
        token = await _setup_admin(client, session)
        img = await _create_image(session, alt_text="Forest trail", original_filename="b.jpg")

        resp = await client.get(
            "/v1/admin/blog/images?search=Forest",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        ids = [i["id"] for i in resp.json()["images"]]
        assert str(img.id) in ids

    async def test_search_finds_by_original_filename(self, client, session):
        token = await _setup_admin(client, session)
        img = await _create_image(session, original_filename="hero-image-final.png")

        resp = await client.get(
            "/v1/admin/blog/images?search=hero-image-final",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        ids = [i["id"] for i in resp.json()["images"]]
        assert str(img.id) in ids

    async def test_search_is_case_insensitive(self, client, session):
        token = await _setup_admin(client, session)
        img = await _create_image(session, title="UPPERCASE TITLE")

        resp = await client.get(
            "/v1/admin/blog/images?search=uppercase",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        ids = [i["id"] for i in resp.json()["images"]]
        assert str(img.id) in ids

    async def test_search_no_results(self, client, session):
        token = await _setup_admin(client, session)
        await _create_image(session, alt_text="something", title="else")

        resp = await client.get(
            "/v1/admin/blog/images?search=nonexistent_xyz",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
        assert resp.json()["images"] == []


# ── Sort Tests ──

@pytest.mark.asyncio
class TestSorting:
    @patch("app.blog.router.delete_artifact")
    async def test_sort_by_file_size_desc(self, mock_s3, client, session):
        token = await _setup_admin(client, session)
        small = await _create_image(session, file_size=1000, alt_text="small")
        medium = await _create_image(session, file_size=5000, alt_text="medium")
        large = await _create_image(session, file_size=10000, alt_text="large")

        resp = await client.get(
            "/v1/admin/blog/images?sort_by=file_size&sort_order=desc",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        ids = [i["id"] for i in resp.json()["images"]]
        assert ids.index(str(large.id)) < ids.index(str(medium.id)) < ids.index(str(small.id))

    async def test_sort_by_created_at_asc(self, client, session):
        token = await _setup_admin(client, session)
        old = await _create_image(session, alt_text="old")
        old.created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        await session.commit()

        new = await _create_image(session, alt_text="new")
        new.created_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
        await session.commit()

        resp = await client.get(
            "/v1/admin/blog/images?sort_by=created_at&sort_order=asc",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        ids = [i["id"] for i in resp.json()["images"]]
        assert ids.index(str(old.id)) < ids.index(str(new.id))


# ── Attachment Filter Tests ──

@pytest.mark.asyncio
class TestAttachmentFilter:
    async def test_filter_unattached(self, client, session):
        token = await _setup_admin(client, session)
        pt = await _seed_post_type(session)

        user_result = await session.execute(
            User.__table__.select().where(User.username == TEST_ADMIN["username"])
        )
        admin_user = user_result.first()
        post = await _create_post(session, admin_user.id, pt.id, slug_suffix="att-filter")

        free = await _create_image(session, alt_text="free")
        attached = await _create_image(session, post_id=post.id, alt_text="attached")

        resp = await client.get(
            "/v1/admin/blog/images?attachment=unattached",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        ids = [i["id"] for i in resp.json()["images"]]
        assert str(free.id) in ids
        assert str(attached.id) not in ids

    async def test_filter_attached(self, client, session):
        token = await _setup_admin(client, session)
        pt = await _seed_post_type(session)

        user_result = await session.execute(
            User.__table__.select().where(User.username == TEST_ADMIN["username"])
        )
        admin_user = user_result.first()
        post = await _create_post(session, admin_user.id, pt.id, slug_suffix="att-filter2")

        free = await _create_image(session, alt_text="free")
        attached = await _create_image(session, post_id=post.id, alt_text="attached")

        resp = await client.get(
            "/v1/admin/blog/images?attachment=attached",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        ids = [i["id"] for i in resp.json()["images"]]
        assert str(attached.id) in ids
        assert str(free.id) not in ids


# ── Update Metadata Tests ──

@pytest.mark.asyncio
class TestUpdateMetadata:
    async def test_update_title_and_caption(self, client, session):
        token = await _setup_admin(client, session)
        img = await _create_image(session)

        resp = await client.put(
            f"/v1/admin/blog/images/{img.id}",
            json={"title": "New Title", "caption": "A caption"},
            headers={**_auth(token), "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "New Title"
        assert body["caption"] == "A caption"

    async def test_partial_update_preserves_other_fields(self, client, session):
        token = await _setup_admin(client, session)
        img = await _create_image(session, alt_text="keep me", title="original")

        # Update only caption — alt_text and title should remain
        resp = await client.put(
            f"/v1/admin/blog/images/{img.id}",
            json={"caption": "just caption"},
            headers={**_auth(token), "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["caption"] == "just caption"
        assert body["alt_text"] == "keep me"
        assert body["title"] == "original"
