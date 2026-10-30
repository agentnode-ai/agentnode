"""Guards of app/publishers/auto_trust.py that need no database: only a
prompt-only skill with a clean content check is ever considered."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.publishers.auto_trust import maybe_auto_verify


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "step_results",
    [
        {"verification_mode": "real", "import_status": "passed"},
        {"verification_mode": "real_auto", "import_status": "passed"},
        {"verification_mode": "prompt_only", "import_status": "failed"},
        {
            "verification_mode": "prompt_only",
            "import_status": "passed",
            "skill_review_required": True,
        },
    ],
)
async def test_not_evidence_for_verification(step_results):
    session = AsyncMock()
    assert await maybe_auto_verify(session, uuid4(), uuid4(), step_results) is False
    session.execute.assert_not_called()
    session.add.assert_not_called()
