"""Automatic 'verified' for publishers — no manual approval for the common case.

A publisher becomes 'verified' when their account email is confirmed and one
of their skills passed verification with a clean content check (no medium or
high finding, no open scanner finding). A skill runs no code, so its checked
text is the evidence. The grant is recorded in trust_auto_granted_at and an
audit row, and it is withdrawn again if a later version of that publisher is
quarantined by the security checks. Only 'unverified' is ever promoted, and an
admin-set level (trust_auto_granted_at is NULL) is never withdrawn here.
'trusted' and 'curated' stay manual. See docs/design/skill-publishing-trust.md.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select

logger = logging.getLogger(__name__)


async def maybe_auto_verify(
    session, publisher_id: UUID, version_id: UUID, step_results: dict
) -> bool:
    """Promote the publisher to 'verified' if this skill version earns it.

    Call after a passing verification of a prompt-only skill. Flushes but does
    not commit; returns True when the publisher was promoted.
    """
    from app.admin.models import AdminAuditLog
    from app.auth.models import User
    from app.packages.models import SecurityFinding
    from app.publishers.models import Publisher

    if step_results.get("verification_mode") != "prompt_only":
        return False
    if step_results.get("import_status") != "passed" or step_results.get(
        "skill_review_required"
    ):
        return False

    publisher = (
        await session.execute(select(Publisher).where(Publisher.id == publisher_id))
    ).scalar_one_or_none()
    if (
        publisher is None
        or publisher.trust_level != "unverified"
        or publisher.is_system_publisher
        or publisher.is_suspended
    ):
        return False

    email_confirmed = (
        await session.execute(
            select(User.is_email_verified).where(User.id == publisher.user_id)
        )
    ).scalar_one_or_none()
    if not email_confirmed:
        return False

    open_findings = (
        await session.execute(
            select(func.count(SecurityFinding.id)).where(
                SecurityFinding.package_version_id == version_id,
                SecurityFinding.is_resolved.is_(False),
                SecurityFinding.severity.in_(("medium", "high", "critical")),
            )
        )
    ).scalar() or 0
    if open_findings:
        return False

    publisher.trust_level = "verified"
    publisher.trust_auto_granted_at = datetime.now(timezone.utc)
    session.add(
        AdminAuditLog(
            admin_user_id=None,
            action="publisher.auto_verified",
            target_type="publisher",
            target_id=publisher.slug,
            metadata_={
                "basis": "email confirmed + skill passed verification with a clean content check",
                "package_version_id": str(version_id),
            },
        )
    )
    await session.flush()
    logger.info(
        "Publisher %s automatically verified (skill version %s)",
        publisher.slug,
        version_id,
    )
    return True


async def withdraw_auto_verification(session, publisher_id: UUID, reason: str) -> bool:
    """Undo an automatic 'verified' after one of the publisher's versions was
    quarantined by the security checks. Admin-set levels are left alone.
    Flushes but does not commit; returns True when the grant was withdrawn.
    """
    from app.admin.models import AdminAuditLog
    from app.publishers.models import Publisher

    publisher = (
        await session.execute(select(Publisher).where(Publisher.id == publisher_id))
    ).scalar_one_or_none()
    if (
        publisher is None
        or publisher.trust_auto_granted_at is None
        or publisher.trust_level != "verified"
    ):
        return False

    publisher.trust_level = "unverified"
    publisher.trust_auto_granted_at = None
    session.add(
        AdminAuditLog(
            admin_user_id=None,
            action="publisher.auto_verification_withdrawn",
            target_type="publisher",
            target_id=publisher.slug,
            metadata_={"reason": reason},
        )
    )
    await session.flush()
    logger.warning(
        "Automatic verification of publisher %s withdrawn: %s", publisher.slug, reason
    )
    return True
