"""Smoke suite, backend part (PLAN-2026-11.md section 2, core paths 1 and 6).

Each test walks one user path end to end through the real FastAPI app (in-process,
httpx ASGITransport) against the local test database. External services are
replaced only at their edge: object storage keeps the uploaded bytes in memory,
search indexing and mail are no-ops. All accounts, keys and packages are synthetic.

Run with the whole suite: ``python scripts/smoke.py`` (see docs/testing/smoke-suite.md).
"""

from __future__ import annotations

import io
import json
import tarfile
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

USER = {
    "email": "smoke-user@agentnode.dev",
    "username": "smokeuser",
    "password": "Smoke-Placeholder-123!",
}


def test_smoke_runs_against_this_checkout():
    """The suite must test the code next to it, not an installed copy."""
    from pathlib import Path

    import app

    backend = Path(__file__).resolve().parents[2]
    assert Path(app.__file__).resolve().is_relative_to(backend), (
        f"app imported from {app.__file__}, not from {backend}"
    )


def _step(path: str, what: str, resp) -> str:
    """A failure message that names the user path and the step that broke."""
    body = resp.text[:300] if hasattr(resp, "text") else str(resp)
    return f"[{path}] {what}: HTTP {getattr(resp, 'status_code', '?')} {body}"


# ---------------------------------------------------------------------------
# Core path 1: register on the website -> log in -> API key -> use the key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smoke_1_register_login_api_key(client):
    path = "1 Registrierung/Login -> API-Key"

    r = await client.post("/v1/auth/register", json=USER)
    assert r.status_code == 201, _step(path, "register", r)

    r = await client.post(
        "/v1/auth/login", json={"email": USER["email"], "password": USER["password"]}
    )
    assert r.status_code == 200, _step(path, "login", r)
    jwt = r.json()["access_token"]

    r = await client.post(
        "/v1/auth/api-keys",
        json={"label": "smoke"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert r.status_code == 201, _step(path, "create API key", r)
    key = r.json()["key"]
    assert key.startswith("ank_"), _step(path, "API key format", r)

    # The key alone (no session, no JWT) identifies the user.
    r = await client.get("/v1/auth/me", headers={"X-API-Key": key})
    assert r.status_code == 200, _step(path, "use API key on /v1/auth/me", r)
    assert r.json()["email"] == USER["email"], _step(path, "key -> same user", r)

    # A wrong key is refused, so the 200 above is the key's doing.
    r = await client.get("/v1/auth/me", headers={"X-API-Key": "ank_" + "0" * 40})
    assert r.status_code == 401, _step(path, "wrong key must be refused", r)


# ---------------------------------------------------------------------------
# Core path 6: publish a package -> it is verified -> others can install it
# ---------------------------------------------------------------------------

SKILL = {
    "manifest_version": "0.2",
    "package_id": "smoke-notes-skill",
    "package_type": "skill",
    "name": "Smoke Notes Skill",
    "publisher": "smoke-publisher",
    "version": "1.0.0",
    "summary": "Write short meeting notes from a transcript.",
    "description": "A prompt-only skill used by the smoke suite.",
    "runtime": "none",
    "install_mode": "prompt_only",
    "hosting_type": "agentnode_hosted",
    "capabilities": {
        "tools": [],
        "resources": [],
        "prompts": [
            {
                "name": "main",
                "capability_id": "smoke_notes_skill.prompt",
                "template": "SKILL.md",
                "description": "Meeting notes instructions",
            }
        ],
    },
    "compatibility": {"frameworks": ["generic"]},
    "permissions": {
        "network": {"level": "none"},
        "filesystem": {"level": "none"},
        "code_execution": {"level": "none"},
        "data_access": {"level": "input_only"},
        "user_approval": {"required": "never"},
    },
}
SKILL_MD = "# Smoke Notes Skill\n\nSummarise the decisions and the open questions.\n"


def _skill_artifact() -> bytes:
    import yaml

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in (
            ("agentnode.yaml", yaml.safe_dump(SKILL, sort_keys=False)),
            ("SKILL.md", SKILL_MD),
        ):
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=f"smoke-notes-skill/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class _MemoryStorage:
    """Object storage at its edge: what is uploaded is what is downloaded."""

    def __init__(self):
        self.blobs: dict[str, bytes] = {}

    async def upload(self, key, data, *a, **k):
        self.blobs[key] = bytes(data)
        return key

    async def download(self, key):
        if key not in self.blobs:
            raise FileNotFoundError(key)
        return self.blobs[key]


@pytest.mark.asyncio
async def test_smoke_6_publish_verify_install(client, session):
    path = "6 Paket veroeffentlichen + verifizieren"
    store = _MemoryStorage()

    with ExitStack() as stack:
        stack.enter_context(
            patch("app.packages.service.upload_artifact", side_effect=store.upload)
        )
        stack.enter_context(
            patch(
                "app.packages.service.upload_preview_file", return_value="preview/key"
            )
        )
        stack.enter_context(
            patch(
                "app.packages.service.sync_package_to_meilisearch",
                new_callable=AsyncMock,
            )
        )
        stack.enter_context(
            patch(
                "app.shared.meili.sync_package_to_meilisearch", new_callable=AsyncMock
            )
        )
        stack.enter_context(
            patch("app.shared.storage.download_artifact", side_effect=store.download)
        )
        stack.enter_context(
            patch(
                "app.shared.email.get_publisher_email",
                new_callable=AsyncMock,
                return_value=None,
            )
        )
        import app.verification.pipeline as _pipeline

        _pipeline._verification_semaphore = None

        # A brand-new, untrusted publisher -- the case an outside author is in.
        await client.post("/v1/auth/register", json=USER)
        r = await client.post(
            "/v1/auth/login",
            json={"email": USER["email"], "password": USER["password"]},
        )
        assert r.status_code == 200, _step(path, "login", r)
        auth = {"Authorization": f"Bearer {r.json()['access_token']}"}

        r = await client.post(
            "/v1/publishers",
            json={"slug": "smoke-publisher", "display_name": "Smoke Publisher"},
            headers=auth,
        )
        assert r.status_code == 201, _step(path, "create publisher", r)
        client.db_session.expire_all()

        r = await client.post(
            "/v1/packages/publish",
            data={"manifest": json.dumps(SKILL)},
            files={
                "artifact": (
                    "smoke-notes-skill.tar.gz",
                    _skill_artifact(),
                    "application/gzip",
                )
            },
            headers=auth,
        )
        assert r.status_code == 201, _step(path, "publish", r)
        assert store.blobs, f"[{path}] publish stored no artifact"

        # Background verification + security scan ran with the request.
        from app.packages.models import Package, PackageVersion

        session.expire_all()
        pv = (
            await session.execute(
                select(PackageVersion)
                .join(Package, Package.id == PackageVersion.package_id)
                .where(Package.slug == "smoke-notes-skill")
            )
        ).scalar_one()
        assert pv.verification_status == "passed", (
            f"[{path}] verification: status={pv.verification_status!r}"
        )
        assert pv.quarantine_status in ("none", "cleared"), (
            f"[{path}] still held: quarantine={pv.quarantine_status!r} "
            f"reason={pv.quarantine_reason!r}"
        )

        # Another user can now resolve and install it.
        r = await client.get("/v1/packages/smoke-notes-skill/install-info")
        assert r.status_code == 200, _step(path, "install-info for others", r)
        assert r.json()["verification_status"] == "passed", _step(
            path, "install-info status", r
        )

        r = await client.post(
            "/v1/auth/register",
            json={
                "email": "smoke-consumer@agentnode.dev",
                "username": "smokeconsumer",
                "password": USER["password"],
            },
        )
        assert r.status_code == 201, _step(path, "register consumer", r)
        r = await client.post(
            "/v1/auth/login",
            json={
                "email": "smoke-consumer@agentnode.dev",
                "password": USER["password"],
            },
        )
        r = await client.post(
            "/v1/packages/smoke-notes-skill/install",
            json={"version": "1.0.0", "source": "sdk", "event_type": "install"},
            headers={"Authorization": f"Bearer {r.json()['access_token']}"},
        )
        assert r.status_code in (200, 201), _step(path, "install by consumer", r)
