"""Import API — converts framework-specific tools into ANP packages."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.import_.schemas import ConvertRequest, ConvertResponse
from app.import_.service import convert
from app.shared.rate_limit import rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/import", tags=["import"])


@router.post("/convert", response_model=ConvertResponse, dependencies=[Depends(rate_limit(max_requests=20, window_seconds=60))])
async def convert_tool(body: ConvertRequest) -> ConvertResponse:
    try:
        return convert(body)
    except Exception:
        logger.exception(
            "import_convert_failed",
            extra={
                "platform": body.platform,
                "content_length": len(body.content or ""),
            },
        )
        raise
