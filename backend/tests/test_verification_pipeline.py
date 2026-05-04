"""Integration tests for the verification pipeline (P1.6).

Tests run_verification() end-to-end with a real async DB session,
mocking only external I/O (S3 download, sync verification runner, email).

Mocking strategy:
  - async_session_factory  -> test session factory (real DB, test engine)
  - download_artifact      -> AsyncMock returning fake bytes (patched at source module)
  - _run_verification_sync -> MagicMock returning controlled step dicts
  - _get_semaphore         -> real semaphore (no patching needed)
  - email helpers          -> AsyncMock (no-op)
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

from app.config import settings
from app.packages.models import (
    Capability,
    CapabilityTaxonomy,
    Package,
    PackageVersion,
)
from app.publishers.models import Publisher
from app.auth.models import User
from app.shared.models import Base
from app.verification.models import VerificationResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = settings.DATABASE_URL

SEED_CAPABILITY_IDS = [
    ("pdf_extraction", "PDF Extraction", "Extract text and data from PDF documents", "document-processing"),
]


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(TEST_DATABASE_URL)
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
    """Return an async_sessionmaker bound to the test engine."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(session_factory):
    async with session_factory() as sess:
        yield sess


@pytest_asyncio.fixture(autouse=True)
async def reset_semaphore():
    """Reset the module-level semaphore cache between tests."""
    import app.verification.pipeline as _mod
    _mod._verification_semaphore = None
    yield
    _mod._verification_semaphore = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ARTIFACT_BYTES = b"fake-tarball-content-for-testing"
ARTIFACT_HASH = hashlib.sha256(ARTIFACT_BYTES).hexdigest()


async def _seed_package(session: AsyncSession, *, quarantine_status="none",
                        quarantine_reason=None, artifact_key="artifacts/test.tar.gz"):
    """Create User -> Publisher -> Package -> PackageVersion -> Capability.

    Returns (package_version_id, package_id).
    """
    user = User(
        id=uuid4(), email=f"test-{uuid4().hex[:8]}@example.com",
        username=f"testuser-{uuid4().hex[:8]}", password_hash="x",
    )
    session.add(user)
    await session.flush()

    publisher = Publisher(
        id=uuid4(), user_id=user.id, display_name="Test Publisher",
        slug=f"test-pub-{uuid4().hex[:8]}",
    )
    session.add(publisher)
    await session.flush()

    pkg = Package(
        id=uuid4(), publisher_id=publisher.id, slug=f"test-pkg-{uuid4().hex[:8]}",
        name="Test Package", package_type="agent", summary="A test package",
    )
    session.add(pkg)
    await session.flush()

    pv = PackageVersion(
        id=uuid4(), package_id=pkg.id, version_number="1.0.0",
        manifest_raw={"name": "test-pkg", "version": "1.0.0", "permissions": {}},
        runtime="python", artifact_object_key=artifact_key,
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
        capability_id="pdf_extraction", name="extract_pdf",
        entrypoint="my_package.tool:run",
        input_schema={"type": "object", "properties": {"url": {"type": "string"}}},
    )
    session.add(cap)
    await session.flush()
    await session.commit()

    return pv.id, pkg.id


def _make_step_results(*, passed=True, smoke_status="passed", smoke_reason=None,
                       tests_status="not_present", extra=None):
    """Build a step_results dict like _run_verification_sync returns."""
    results = {
        "install_status": "passed" if passed else "failed",
        "import_status": "passed" if passed else "failed",
        "smoke_status": smoke_status,
        "tests_status": tests_status,
        "install_log": "ok" if passed else "ERROR: install failed",
        "import_log": "ok" if passed else "ERROR: import failed",
        "smoke_log": "",
        "tests_log": "",
        "install_duration_ms": 500,
        "import_duration_ms": 200,
        "smoke_duration_ms": 1000,
        "tests_duration_ms": None,
        "error_summary": None if passed else "Verification failed: install, import",
        "warnings_count": 0,
        "warnings_summary": None,
        "smoke_reason": smoke_reason,
        "installer": "pip",
        "verification_mode": "real_auto",
        "has_explicit_cases": False,
        "contract_details": None,
        "smoke_confidence": None,
        "reliability": None,
        "determinism_score": None,
        "contract_valid": None,
        "stability_log": None,
        "tests_auto_generated": False,
        "isolation": {
            "install": "subprocess",
            "import": "subprocess",
            "smoke": "subprocess",
            "tests": "subprocess",
            "overall": "subprocess",
        },
    }
    if extra:
        results.update(extra)
    return results


def _pipeline_patches(session_factory, step_results=None, artifact_bytes=ARTIFACT_BYTES,
                      download_side_effect=None):
    """Return a list of patch context managers for a standard pipeline run.

    Patches:
      1. async_session_factory -> test session factory
      2. download_artifact     -> returns artifact_bytes (or raises if side_effect given)
      3. _run_verification_sync -> returns step_results via executor
      4. email helpers         -> no-op AsyncMock

    Returns a dict of the patches keyed by name for assertions.
    """
    patches = {}

    patches["session_factory"] = patch(
        "app.verification.pipeline.async_session_factory", session_factory,
    )
    if download_side_effect:
        patches["download"] = patch(
            "app.shared.storage.download_artifact",
            new_callable=AsyncMock, side_effect=download_side_effect,
        )
    else:
        patches["download"] = patch(
            "app.shared.storage.download_artifact",
            new_callable=AsyncMock, return_value=artifact_bytes,
        )

    if step_results is not None:
        # _run_verification_sync is called by loop.run_in_executor(None, fn, ...)
        # MagicMock is a sync callable, which is correct for run_in_executor.
        patches["sync_runner"] = patch(
            "app.verification.pipeline._run_verification_sync",
            return_value=step_results,
        )

    patches["get_pub_email"] = patch(
        "app.shared.email.get_publisher_email",
        new_callable=AsyncMock, return_value="test@example.com",
    )
    patches["send_email"] = patch(
        "app.shared.email.send_auto_quarantine_email",
        new_callable=AsyncMock, return_value=True,
    )

    # Prevent Meilisearch/version_queries calls during auto-clear path
    patches["recalc"] = patch(
        "app.packages.version_queries.recalculate_latest_version_id",
        new_callable=AsyncMock,
    )
    patches["meili_sync"] = patch(
        "app.shared.meili.sync_package_to_meilisearch",
        new_callable=AsyncMock,
    )
    patches["meili_doc"] = patch(
        "app.packages.service.build_meili_document",
        return_value={},
    )

    return patches


class _PatchStack:
    """Enter a dict of patches as a combined context manager."""

    def __init__(self, patches: dict):
        self._patches = patches
        self._mocks: dict = {}

    async def __aenter__(self):
        for name, p in self._patches.items():
            self._mocks[name] = p.start()
        return self._mocks

    async def __aexit__(self, *args):
        for p in reversed(list(self._patches.values())):
            p.stop()

    def __enter__(self):
        for name, p in self._patches.items():
            self._mocks[name] = p.start()
        return self._mocks

    def __exit__(self, *args):
        for p in reversed(list(self._patches.values())):
            p.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_package_passes_verification(session_factory, session):
    """Pipeline sets verification_status='passed' and quarantine_status stays
    'none' when all verification steps pass."""
    version_id, _ = await _seed_package(session)
    step_results = _make_step_results(passed=True)

    patches = _pipeline_patches(session_factory, step_results=step_results)
    with _PatchStack(patches):
        from app.verification.pipeline import run_verification
        await run_verification(version_id, triggered_by="publish")

    async with session_factory() as s:
        pv = (await s.execute(
            select(PackageVersion).where(PackageVersion.id == version_id)
        )).scalar_one()

        assert pv.verification_status == "passed"
        assert pv.quarantine_status == "none"
        assert pv.verification_run_count >= 1
        assert pv.last_verified_at is not None
        assert pv.verification_score is not None
        assert pv.verification_tier is not None

        # VerificationResult row created and linked
        vr = (await s.execute(
            select(VerificationResult)
            .where(VerificationResult.package_version_id == version_id)
            .order_by(VerificationResult.created_at.desc())
            .limit(1)
        )).scalar_one()

        assert vr.status == "passed"
        assert vr.install_status == "passed"
        assert vr.import_status == "passed"
        assert vr.completed_at is not None
        assert vr.duration_ms is not None
        assert vr.runner_version is not None
        assert vr.verification_score is not None


@pytest.mark.asyncio
async def test_package_fails_verification_and_quarantined(session_factory, session):
    """When install/import fail, the pipeline sets verification_status='failed'
    and auto-quarantines the version (triggered_by='publish')."""
    version_id, _ = await _seed_package(session, quarantine_status="none")
    step_results = _make_step_results(passed=False)

    patches = _pipeline_patches(session_factory, step_results=step_results)
    with _PatchStack(patches):
        from app.verification.pipeline import run_verification
        await run_verification(version_id, triggered_by="publish")

    async with session_factory() as s:
        pv = (await s.execute(
            select(PackageVersion).where(PackageVersion.id == version_id)
        )).scalar_one()

        assert pv.verification_status == "failed"
        assert pv.quarantine_status == "quarantined"
        assert pv.quarantine_reason is not None
        assert "Auto-quarantined" in pv.quarantine_reason
        assert "verification failed" in pv.quarantine_reason

        vr = (await s.execute(
            select(VerificationResult)
            .where(VerificationResult.package_version_id == version_id)
            .order_by(VerificationResult.created_at.desc())
            .limit(1)
        )).scalar_one()

        assert vr.status == "failed"
        assert vr.error_summary is not None


@pytest.mark.asyncio
async def test_admin_quarantine_preserved_on_verification_pass(session_factory, session):
    """BizLogic 4.2: admin-imposed quarantine must NOT be auto-cleared even
    when verification passes. Only auto-quarantine and new_publisher_review
    reasons are clearable."""
    version_id, _ = await _seed_package(
        session,
        quarantine_status="quarantined",
        quarantine_reason="Admin: security concern - suspicious network calls",
    )
    step_results = _make_step_results(passed=True)

    patches = _pipeline_patches(session_factory, step_results=step_results)
    with _PatchStack(patches):
        from app.verification.pipeline import run_verification
        await run_verification(version_id, triggered_by="publish")

    async with session_factory() as s:
        pv = (await s.execute(
            select(PackageVersion).where(PackageVersion.id == version_id)
        )).scalar_one()

        # Verification itself passed
        assert pv.verification_status == "passed"

        # Admin quarantine must be preserved
        assert pv.quarantine_status == "quarantined"
        assert "Admin" in (pv.quarantine_reason or "")


@pytest.mark.asyncio
async def test_security_scan_quarantine_preserved(session_factory, session):
    """Security-scan quarantine (non-verification reason) must not be cleared
    by a passing verification. Same BizLogic 4.2 principle as admin quarantine."""
    version_id, _ = await _seed_package(
        session,
        quarantine_status="quarantined",
        quarantine_reason="Security scan: critical vulnerability CVE-2025-XXXX",
    )
    step_results = _make_step_results(passed=True)

    patches = _pipeline_patches(session_factory, step_results=step_results)
    with _PatchStack(patches):
        from app.verification.pipeline import run_verification
        await run_verification(version_id, triggered_by="publish")

    async with session_factory() as s:
        pv = (await s.execute(
            select(PackageVersion).where(PackageVersion.id == version_id)
        )).scalar_one()

        assert pv.verification_status == "passed"
        assert pv.quarantine_status == "quarantined"
        assert "Security scan" in (pv.quarantine_reason or "")


@pytest.mark.asyncio
async def test_verification_updates_trust_score(session_factory, session):
    """Pipeline updates verification_score, verification_tier, and
    score_breakdown on both VerificationResult and PackageVersion."""
    version_id, _ = await _seed_package(session)

    step_results = _make_step_results(
        passed=True,
        smoke_status="passed",
        tests_status="passed",
        extra={
            "reliability": 1.0,
            "determinism_score": 1.0,
            "contract_valid": True,
            "tests_auto_generated": False,
            "has_explicit_cases": True,
            "verification_mode": "cases_real",
        },
    )

    patches = _pipeline_patches(session_factory, step_results=step_results)
    with _PatchStack(patches):
        from app.verification.pipeline import run_verification
        await run_verification(version_id, triggered_by="publish")

    async with session_factory() as s:
        pv = (await s.execute(
            select(PackageVersion).where(PackageVersion.id == version_id)
        )).scalar_one()

        # Denormalized score fields on PackageVersion
        assert pv.verification_score is not None
        assert pv.verification_score >= 90  # All steps passed with full quality metrics
        assert pv.verification_tier == "gold"
        assert pv.latest_verification_result_id is not None

        # Full ScoreResult on VerificationResult
        vr = (await s.execute(
            select(VerificationResult)
            .where(VerificationResult.id == pv.latest_verification_result_id)
        )).scalar_one()

        assert vr.verification_score == pv.verification_score
        assert vr.verification_tier == "gold"
        assert vr.confidence is not None
        assert vr.score_breakdown is not None
        breakdown = vr.score_breakdown.get("breakdown", {})
        assert "install" in breakdown
        assert "import" in breakdown
        assert "smoke" in breakdown
        assert "reliability" in breakdown
        assert vr.environment_info is not None
        assert vr.environment_info.get("python_version") is not None


@pytest.mark.asyncio
async def test_pipeline_handles_s3_download_error_gracefully(session_factory, session):
    """When S3 download_artifact raises, the pipeline catches the exception
    and marks verification_status='error' without crashing."""
    version_id, _ = await _seed_package(session)

    patches = _pipeline_patches(
        session_factory,
        download_side_effect=Exception("S3 connection refused"),
    )
    with _PatchStack(patches):
        from app.verification.pipeline import run_verification
        # Must NOT raise
        await run_verification(version_id, triggered_by="publish")

    async with session_factory() as s:
        pv = (await s.execute(
            select(PackageVersion).where(PackageVersion.id == version_id)
        )).scalar_one()

        assert pv.verification_status == "error"

        # The error recovery path creates/updates a VerificationResult
        vr_result = await s.execute(
            select(VerificationResult)
            .where(VerificationResult.package_version_id == version_id)
            .order_by(VerificationResult.created_at.desc())
            .limit(1)
        )
        vr = vr_result.scalar_one_or_none()
        if vr is not None:
            assert vr.status in ("error", "running")


@pytest.mark.asyncio
async def test_auto_quarantine_cleared_on_reverify_pass(session_factory, session):
    """Auto-quarantine from a previous failed verification IS cleared when
    the package passes on a subsequent verification run."""
    version_id, _ = await _seed_package(
        session,
        quarantine_status="quarantined",
        quarantine_reason="Auto-quarantined: verification failed (Verification failed: install)",
    )
    step_results = _make_step_results(passed=True)

    patches = _pipeline_patches(session_factory, step_results=step_results)
    with _PatchStack(patches):
        from app.verification.pipeline import run_verification
        await run_verification(version_id, triggered_by="publish")

    async with session_factory() as s:
        pv = (await s.execute(
            select(PackageVersion).where(PackageVersion.id == version_id)
        )).scalar_one()

        assert pv.verification_status == "passed"
        assert pv.quarantine_status == "cleared"
        assert pv.quarantine_reason is None


@pytest.mark.asyncio
async def test_hash_mismatch_marks_error(session_factory, session):
    """If the downloaded artifact SHA-256 does not match the stored hash,
    pipeline marks the verification as error with a clear summary."""
    version_id, _ = await _seed_package(session)

    wrong_bytes = b"tampered-content-definitely-wrong"
    patches = _pipeline_patches(
        session_factory, artifact_bytes=wrong_bytes,
    )
    with _PatchStack(patches):
        from app.verification.pipeline import run_verification
        await run_verification(version_id, triggered_by="publish")

    async with session_factory() as s:
        pv = (await s.execute(
            select(PackageVersion).where(PackageVersion.id == version_id)
        )).scalar_one()

        assert pv.verification_status == "error"

        vr = (await s.execute(
            select(VerificationResult)
            .where(VerificationResult.package_version_id == version_id)
            .order_by(VerificationResult.created_at.desc())
            .limit(1)
        )).scalar_one()

        assert vr.status == "error"
        assert "hash mismatch" in (vr.error_summary or "").lower()


@pytest.mark.asyncio
async def test_verification_disabled_is_noop(session_factory, session):
    """When VERIFICATION_ENABLED=False, run_verification returns immediately
    without touching the DB or calling S3."""
    version_id, _ = await _seed_package(session)

    with (
        patch("app.verification.pipeline.settings") as mock_settings,
        patch("app.shared.storage.download_artifact", new_callable=AsyncMock) as mock_dl,
    ):
        mock_settings.VERIFICATION_ENABLED = False

        from app.verification.pipeline import run_verification
        await run_verification(version_id, triggered_by="publish")

        mock_dl.assert_not_called()

    async with session_factory() as s:
        pv = (await s.execute(
            select(PackageVersion).where(PackageVersion.id == version_id)
        )).scalar_one()
        assert pv.verification_status == "pending"


@pytest.mark.asyncio
async def test_no_auto_quarantine_on_admin_reverify(session_factory, session):
    """When triggered_by='admin_reverify' and verification fails, the package
    should NOT be auto-quarantined (auto-quarantine is only for 'publish')."""
    version_id, _ = await _seed_package(session, quarantine_status="none")
    step_results = _make_step_results(passed=False)

    patches = _pipeline_patches(session_factory, step_results=step_results)
    with _PatchStack(patches):
        from app.verification.pipeline import run_verification
        await run_verification(version_id, triggered_by="admin_reverify")

    async with session_factory() as s:
        pv = (await s.execute(
            select(PackageVersion).where(PackageVersion.id == version_id)
        )).scalar_one()

        assert pv.verification_status == "failed"
        # quarantine_status should remain "none" — no auto-quarantine on admin re-verify
        assert pv.quarantine_status == "none"
