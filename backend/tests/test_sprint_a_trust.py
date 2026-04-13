"""Sprint A — Trust auto-clear / scanner re-quarantine tests.

Covers:
  P0-02 — auto-clear must refuse when the scanner has recorded open
          medium/high/critical findings against the version.
  P0-03 — scanner must be able to re-quarantine a version that had
          previously been auto-cleared.
  P1-V1 — auto-clear must NOT fire for triggered_by='owner_request' or
          'admin_reverify'.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.models import User
from app.config import settings
from app.packages.models import (
    Capability,
    CapabilityTaxonomy,
    Package,
    PackageVersion,
    SecurityFinding,
)
from app.publishers.models import Publisher
from app.shared.models import Base


ARTIFACT_BYTES = b"fake-tarball-content-for-testing"
ARTIFACT_HASH = hashlib.sha256(ARTIFACT_BYTES).hexdigest()

SEED_CAPABILITY_IDS = [
    ("pdf_extraction", "PDF Extraction", "Extract text and data from PDF documents", "document-processing"),
]


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(settings.DATABASE_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)
        for cap_id, display_name, description, category in SEED_CAPABILITY_IDS:
            await conn.execute(
                CapabilityTaxonomy.__table__.insert().values(
                    id=cap_id, display_name=display_name,
                    description=description, category=category,
                )
            )
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(session_factory):
    async with session_factory() as sess:
        yield sess


@pytest_asyncio.fixture(autouse=True)
async def reset_semaphore():
    import app.verification.pipeline as _mod
    _mod._verification_semaphore = None
    yield
    _mod._verification_semaphore = None


async def _seed(session, *, quarantine_status="none", quarantine_reason=None):
    user = User(
        id=uuid4(), email=f"t-{uuid4().hex[:8]}@example.com",
        username=f"u-{uuid4().hex[:8]}", password_hash="x",
    )
    session.add(user)
    await session.flush()
    pub = Publisher(
        id=uuid4(), user_id=user.id, display_name="T",
        slug=f"pub-{uuid4().hex[:8]}",
    )
    session.add(pub)
    await session.flush()
    pkg = Package(
        id=uuid4(), publisher_id=pub.id, slug=f"pkg-{uuid4().hex[:8]}",
        name="T", package_type="agent", summary="t",
    )
    session.add(pkg)
    await session.flush()
    pv = PackageVersion(
        id=uuid4(), package_id=pkg.id, version_number="1.0.0",
        manifest_raw={"name": "t", "version": "1.0.0"},
        runtime="python", artifact_object_key="artifacts/x.tar.gz",
        artifact_hash_sha256=ARTIFACT_HASH,
        artifact_size_bytes=len(ARTIFACT_BYTES),
        quarantine_status=quarantine_status,
        quarantine_reason=quarantine_reason,
        quarantined_at=datetime.now(timezone.utc) if quarantine_status != "none" else None,
        verification_status="pending",
    )
    session.add(pv)
    await session.flush()
    cap = Capability(
        id=uuid4(), package_version_id=pv.id, capability_type="tool",
        capability_id="pdf_extraction", name="extract",
        entrypoint="m.t:run",
        input_schema={"type": "object", "properties": {}},
    )
    session.add(cap)
    await session.commit()
    return pv.id, pkg.id


def _step_results_passing():
    return {
        "install_status": "passed", "import_status": "passed",
        "smoke_status": "passed", "tests_status": "not_present",
        "install_log": "ok", "import_log": "ok",
        "smoke_log": "", "tests_log": "",
        "install_duration_ms": 1, "import_duration_ms": 1,
        "smoke_duration_ms": 1, "tests_duration_ms": None,
        "error_summary": None,
        "warnings_count": 0, "warnings_summary": None,
        "smoke_reason": None,
        "installer": "pip", "verification_mode": "real",
        "contract_details": None, "smoke_confidence": None,
        "reliability": None, "determinism_score": None,
        "contract_valid": None, "stability_log": None,
        "tests_auto_generated": False,
        "isolation": {
            "install": "subprocess", "import": "subprocess",
            "smoke": "subprocess", "tests": "subprocess", "overall": "subprocess",
        },
    }


def _patches(session_factory):
    return {
        "sf": patch("app.verification.pipeline.async_session_factory", session_factory),
        "dl": patch(
            "app.shared.storage.download_artifact",
            new_callable=AsyncMock, return_value=ARTIFACT_BYTES,
        ),
        "sync": patch(
            "app.verification.pipeline._run_verification_sync",
            return_value=_step_results_passing(),
        ),
        "ge": patch(
            "app.shared.email.get_publisher_email",
            new_callable=AsyncMock, return_value="x@example.com",
        ),
        "se": patch(
            "app.shared.email.send_auto_quarantine_email",
            new_callable=AsyncMock, return_value=True,
        ),
        "recalc": patch(
            "app.packages.version_queries.recalculate_latest_version_id",
            new_callable=AsyncMock,
        ),
        "meili_sync": patch(
            "app.shared.meili.sync_package_to_meilisearch",
            new_callable=AsyncMock,
        ),
        "meili_doc": patch(
            "app.packages.service.build_meili_document", return_value={},
        ),
    }


class _Stack:
    def __init__(self, p):
        self._p = p

    def __enter__(self):
        for v in self._p.values():
            v.start()
        return self

    def __exit__(self, *a):
        for v in reversed(list(self._p.values())):
            v.stop()


@pytest.mark.asyncio
async def test_open_scanner_finding_blocks_auto_clear(session_factory, session):
    """P0-02: an open high-severity SecurityFinding must block auto-clear,
    even if the quarantine_reason is otherwise in AUTO_CLEARABLE_REASONS."""
    version_id, _ = await _seed(
        session,
        quarantine_status="quarantined",
        quarantine_reason="new_publisher_review",
    )

    async with session_factory() as s:
        s.add(SecurityFinding(
            id=uuid4(),
            package_version_id=version_id,
            severity="high",
            finding_type="secret_detected",
            description="leaked api key",
            scanner="agentnode-static-v1",
            is_resolved=False,
        ))
        await s.commit()

    with _Stack(_patches(session_factory)):
        from app.verification.pipeline import run_verification
        await run_verification(version_id, triggered_by="publish")

    async with session_factory() as s:
        pv = (await s.execute(
            select(PackageVersion).where(PackageVersion.id == version_id)
        )).scalar_one()

        # Verification run passed, but auto-clear was refused because the
        # scanner has recorded an open high-severity finding.
        assert pv.verification_status == "passed"
        assert pv.quarantine_status == "quarantined"
        assert pv.quarantine_reason == "new_publisher_review"


@pytest.mark.asyncio
async def test_medium_finding_blocks_auto_clear(session_factory, session):
    """Acceptance criterion: severity >= medium blocks auto-clear."""
    version_id, _ = await _seed(
        session,
        quarantine_status="quarantined",
        quarantine_reason="new_publisher_review",
    )
    async with session_factory() as s:
        s.add(SecurityFinding(
            id=uuid4(),
            package_version_id=version_id,
            severity="medium",
            finding_type="undeclared_network_access",
            description="network call",
            scanner="agentnode-static-v1",
            is_resolved=False,
        ))
        await s.commit()

    with _Stack(_patches(session_factory)):
        from app.verification.pipeline import run_verification
        await run_verification(version_id, triggered_by="publish")

    async with session_factory() as s:
        pv = (await s.execute(
            select(PackageVersion).where(PackageVersion.id == version_id)
        )).scalar_one()
        assert pv.quarantine_status == "quarantined"


@pytest.mark.asyncio
async def test_resolved_finding_does_not_block_auto_clear(session_factory, session):
    """A finding that has been resolved by an admin must not block the
    auto-clear path."""
    version_id, _ = await _seed(
        session,
        quarantine_status="quarantined",
        quarantine_reason="new_publisher_review",
    )
    async with session_factory() as s:
        s.add(SecurityFinding(
            id=uuid4(),
            package_version_id=version_id,
            severity="high",
            finding_type="secret_detected",
            description="resolved",
            scanner="agentnode-static-v1",
            is_resolved=True,
        ))
        await s.commit()

    with _Stack(_patches(session_factory)):
        from app.verification.pipeline import run_verification
        await run_verification(version_id, triggered_by="publish")

    async with session_factory() as s:
        pv = (await s.execute(
            select(PackageVersion).where(PackageVersion.id == version_id)
        )).scalar_one()
        assert pv.quarantine_status == "cleared"


@pytest.mark.asyncio
async def test_owner_reverify_cannot_auto_clear(session_factory, session):
    """P1-V1: owner-initiated reverify must never auto-clear a quarantined
    version — only fresh 'publish' triggers can."""
    version_id, _ = await _seed(
        session,
        quarantine_status="quarantined",
        quarantine_reason="new_publisher_review",
    )

    with _Stack(_patches(session_factory)):
        from app.verification.pipeline import run_verification
        await run_verification(version_id, triggered_by="owner_request")

    async with session_factory() as s:
        pv = (await s.execute(
            select(PackageVersion).where(PackageVersion.id == version_id)
        )).scalar_one()
        assert pv.quarantine_status == "quarantined"


@pytest.mark.asyncio
async def test_admin_reverify_cannot_auto_clear(session_factory, session):
    """Admin reverify must also not auto-clear — admins can clear via the
    admin review UI explicitly, not via a re-verify as a side effect."""
    version_id, _ = await _seed(
        session,
        quarantine_status="quarantined",
        quarantine_reason="new_publisher_review",
    )

    with _Stack(_patches(session_factory)):
        from app.verification.pipeline import run_verification
        await run_verification(version_id, triggered_by="admin_reverify")

    async with session_factory() as s:
        pv = (await s.execute(
            select(PackageVersion).where(PackageVersion.id == version_id)
        )).scalar_one()
        assert pv.quarantine_status == "quarantined"


@pytest.mark.asyncio
async def test_scanner_can_requarantine_cleared_version(session_factory, session):
    """P0-03: a version that was previously auto-cleared must be
    re-quarantined on a follow-up scan that turns up high-severity
    findings. Previously the scanner silently skipped any version whose
    quarantine_status was not 'none'.
    """
    version_id, _ = await _seed(
        session,
        quarantine_status="cleared",
        quarantine_reason=None,
    )

    # Build an artifact that looks like a secret-leaking Python file so the
    # scanner's static layer fires.
    import io
    import tarfile
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:gz") as tar:
        data = b"API_KEY = 'sk-abcdefghijklmnopqrstuvwxyz'\n"
        info = tarfile.TarInfo(name="leak.py")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    malicious_bytes = tar_buf.getvalue()

    with (
        patch("app.trust.scanner.async_session_factory", session_factory),
        patch(
            "app.shared.storage.download_artifact",
            new_callable=AsyncMock, return_value=malicious_bytes,
        ),
        patch(
            "app.trust.scanner._run_bandit",
            return_value=[],
        ),
        patch(
            "app.shared.email.get_publisher_email",
            new_callable=AsyncMock, return_value="x@example.com",
        ),
        patch(
            "app.shared.email.send_auto_quarantine_email",
            new_callable=AsyncMock, return_value=True,
        ),
        patch(
            "app.packages.version_queries.recalculate_latest_version_id",
            new_callable=AsyncMock,
        ),
    ):
        from app.trust.scanner import run_security_scan
        await run_security_scan(version_id)

    async with session_factory() as s:
        pv = (await s.execute(
            select(PackageVersion).where(PackageVersion.id == version_id)
        )).scalar_one()
        assert pv.quarantine_status == "quarantined"
        assert "Security scan" in (pv.quarantine_reason or "")
