import io
import json
import logging
import os
import re
import tarfile

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.builder.guardrails import has_critical_findings, scan_generated_code, validate_input
from app.builder.schemas import BuilderArtifactRequest, BuilderGenerateRequest, BuilderGenerateResponse
from app.builder.service import generate_capability
from app.config import settings
from app.shared.exceptions import AppError
from app.shared.rate_limit import rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/builder", tags=["builder"])


@router.post(
    "/generate",
    response_model=BuilderGenerateResponse,
    dependencies=[Depends(rate_limit(max_requests=5, window_seconds=60))],
)
async def builder_generate(
    body: BuilderGenerateRequest,
    user: User = Depends(get_current_user),
) -> BuilderGenerateResponse:
    """Generate an ANP v0.2 manifest and code scaffold from a description."""
    description = body.description.strip()
    if len(description) < 10:
        raise AppError("BUILDER_INPUT_TOO_SHORT", "Please describe your capability in more detail.", 400)

    # --- Input guardrails ---
    input_error = validate_input(description)
    if input_error:
        raise AppError("BUILDER_INPUT_BLOCKED", input_error, 400)

    # --- Generate ---
    result: BuilderGenerateResponse | None = None

    # Use AI generation when Anthropic API key is configured, fall back to heuristic
    if settings.ANTHROPIC_API_KEY:
        try:
            from app.builder.ai import generate_with_ai
            result = await generate_with_ai(description=description)
        except Exception as exc:
            logger.warning("AI generation failed, falling back to heuristic: %s", exc)

    if result is None:
        result = generate_capability(description=description)

    # --- Output guardrails: scan generated code ---
    findings = scan_generated_code(result.code_files)
    if findings:
        logger.warning(
            "Security findings in generated code for '%s' (user=%s): %s",
            description[:60], user.username, findings,
        )
        if has_critical_findings(findings):
            raise AppError(
                "BUILDER_OUTPUT_BLOCKED",
                "The generated code contains potentially unsafe patterns. Please try a different description.",
                400,
            )
        # Non-critical findings: attach as warnings
        result.metadata.warnings.extend(
            f"[Security] {f['description']}" for f in findings
        )

    return result


@router.post(
    "/artifact",
    dependencies=[Depends(rate_limit(max_requests=20, window_seconds=60))],
)
async def builder_artifact(
    body: BuilderArtifactRequest,
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Build a .tar.gz artifact from builder output (manifest + code files)."""
    # Detect tool function name from code files for test generation
    tool_func = "run"
    module_name = body.package_id.replace("-", "_")
    for f in body.code_files:
        if f.path.endswith("/tool.py"):
            import re as _re
            m = _re.search(r"^def (\w+)\(", f.content, _re.MULTILINE)
            if m:
                tool_func = m.group(1)
            # Extract module name from path like src/my_pack/tool.py
            parts = f.path.split("/")
            if len(parts) >= 2:
                module_name = parts[-2]
            break

    # Generate a basic test file
    test_content = (
        f'"""Tests for {body.package_id}."""\n'
        f"import pytest\n\n"
        f"from {module_name}.tool import {tool_func}\n\n\n"
        f"def test_{tool_func}_exists():\n"
        f'    """Verify the tool function is importable."""\n'
        f"    assert callable({tool_func})\n"
    )

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # Add manifest.json
        manifest_bytes = json.dumps(body.manifest_json, indent=2).encode()
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))

        # Add code files
        for f in body.code_files:
            safe_path = os.path.normpath(f.path)
            if safe_path.startswith(("/", "\\")) or ".." in safe_path:
                raise AppError(
                    "BUILDER_INVALID_PATH",
                    f"Invalid file path: {f.path}",
                    400,
                )
            data = f.content.encode()
            info = tarfile.TarInfo(name=safe_path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        # Add test file only if none already included in code_files
        has_test = any(
            f.path.startswith("tests/") and f.path.endswith(".py") and f.path != "tests/__init__.py"
            for f in body.code_files
        )
        if not has_test:
            test_bytes = test_content.encode()
            info = tarfile.TarInfo(name=f"tests/test_{tool_func}.py")
            info.size = len(test_bytes)
            tar.addfile(info, io.BytesIO(test_bytes))

        # Add tests/__init__.py if not already present
        has_test_init = any(f.path == "tests/__init__.py" for f in body.code_files)
        if not has_test_init:
            init_bytes = b""
            info = tarfile.TarInfo(name="tests/__init__.py")
            info.size = 0
            tar.addfile(info, io.BytesIO(init_bytes))

    buf.seek(0)
    safe_name = re.sub(r'[^a-z0-9-]', '', body.package_id)[:60] or 'package'
    return StreamingResponse(
        buf,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.tar.gz"'},
    )
