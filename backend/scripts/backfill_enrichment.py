"""Backfill enrichment fields for all existing package versions.

Fills: readme_md, file_list, use_cases, examples, env_requirements
Uses S3 artifacts for file_list/readme_md, Claude AI for use_cases/examples/readme.

Run from /opt/agentnode/backend with venv activated:
    python scripts/backfill_enrichment.py
"""
import asyncio
import io
import json
import logging
import os
import sys
import tarfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database import engine
from app.shared.storage import (
    download_artifact,
    upload_preview_file,
    PREVIEW_EXTENSIONS,
    PREVIEW_MAX_BYTES,
    PREVIEW_MAX_LINES,
)
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# --- Artifact extraction (same logic as service.py) ---

async def extract_artifact_metadata(artifact_bytes: bytes, version_id: str) -> dict:
    """Extract file_list, readme_md, and upload preview files from a tar.gz artifact."""
    result = {"file_list": [], "readme_md": None, "preview_count": 0}

    try:
        with tarfile.open(fileobj=io.BytesIO(artifact_bytes), mode="r:gz") as tar:
            for member in tar.getmembers():
                if member.isdir():
                    continue

                path = member.name
                parts = path.split("/", 1)
                normalized = parts[1] if len(parts) > 1 else parts[0]

                result["file_list"].append({
                    "path": normalized,
                    "size": member.size,
                })

                # Extract README.md (case-insensitive, top-level only)
                basename = os.path.basename(normalized).lower()
                if basename == "readme.md" and "/" not in normalized:
                    f = tar.extractfile(member)
                    if f:
                        raw = f.read()
                        if len(raw) <= 1_000_000:
                            try:
                                result["readme_md"] = raw.decode("utf-8")
                            except UnicodeDecodeError:
                                pass

                # Upload preview files
                ext = os.path.splitext(normalized)[1].lower()
                if ext in PREVIEW_EXTENSIONS and member.size <= PREVIEW_MAX_BYTES:
                    f = tar.extractfile(member)
                    if f:
                        raw = f.read()
                        if b"\x00" not in raw:
                            try:
                                content = raw.decode("utf-8")
                                lines = content.splitlines(True)
                                if len(lines) > PREVIEW_MAX_LINES:
                                    content = "".join(lines[:PREVIEW_MAX_LINES])
                                await upload_preview_file(version_id, normalized, content)
                                result["preview_count"] += 1
                            except UnicodeDecodeError:
                                pass
    except (tarfile.TarError, EOFError) as e:
        logger.warning(f"Failed to extract artifact for {version_id}: {e}")

    return result


# --- AI generation ---

async def generate_enrichment_with_ai(
    name: str,
    slug: str,
    summary: str,
    description: str | None,
    capabilities: list[dict],
    entrypoint: str | None,
    permissions: dict | None,
    has_readme: bool,
) -> dict:
    """Use Claude to generate use_cases, examples, and optionally readme_md."""
    import anthropic

    if not settings.ANTHROPIC_API_KEY:
        logger.warning("No ANTHROPIC_API_KEY set, skipping AI generation")
        return {}

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    cap_names = [c.get("name", "") for c in capabilities]
    cap_ids = [c.get("capability_id", "") for c in capabilities]
    ep_str = entrypoint or "unknown"

    # Build module/function from entrypoint
    if entrypoint and "." in entrypoint:
        parts = entrypoint.rsplit(".", 1)
        module_path = entrypoint
        # For v0.2 style entrypoints
        if ":" in entrypoint:
            module_path, func_name = entrypoint.rsplit(":", 1)
        else:
            func_name = "run"
    else:
        module_path = slug.replace("-", "_") + ".tool"
        func_name = "run"

    # Determine if it needs API keys based on permissions
    needs_network = False
    env_hint = ""
    if permissions:
        net_level = permissions.get("network_level", "none")
        if net_level in ("restricted", "unrestricted"):
            needs_network = True
            env_hint = "This tool makes network requests and may need API keys."

    readme_instruction = ""
    if not has_readme:
        readme_instruction = """
Also generate "readme_md": a full Markdown README with these sections:
# {package_name}
{summary}
## Quick Start
## Usage
## API Reference
## License
MIT"""

    prompt = f"""Generate enrichment data for this AgentNode package. Return ONLY valid JSON.

Package: {name}
Slug: {slug}
Summary: {summary}
Description: {description or summary}
Capabilities: {json.dumps(cap_names)}
Capability IDs: {json.dumps(cap_ids)}
Entrypoint: {ep_str}
Module: {module_path}
Function: {func_name}
Needs network: {needs_network}
{env_hint}

Generate this JSON:
{{
  "use_cases": [
    // 3-5 strings, each "verb + concrete object" format
    // e.g. "Extract tables from PDF financial reports"
    // Be specific to this tool, not generic
  ],
  "examples": [
    {{
      "title": "// Short descriptive title",
      "language": "python",
      "code": "// Working Python code showing basic usage\\n// Use the actual entrypoint: from {module_path} import {func_name}\\n// Show realistic input data, not placeholders"
    }},
    {{
      "title": "// Second example showing different use case",
      "language": "python",
      "code": "// Another working example"
    }}
  ],
  "env_requirements": [
    // Only if the tool needs API keys or env vars
    // {{"name": "ENV_VAR_NAME", "required": true, "description": "What this is for"}}
    // Empty array [] if no env vars needed
  ]{readme_instruction and ','}
  {'"readme_md": "..."' if not has_readme else ''}
}}

Rules:
- use_cases: MUST be specific to this tool. "verb + concrete object". Not generic like "Process data".
- examples: MUST use the actual import path "from {module_path} import {func_name}". Show realistic usage.
- env_requirements: Only include if the tool genuinely needs API keys (network-enabled tools). Empty array otherwise.
- All code must look real and runnable (though it won't actually execute here).
{f'- readme_md: Full Markdown README. Include install command: agentnode install {slug}' if not has_readme else ''}"""

    try:
        message = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            import re
            raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
            raw = re.sub(r"\n?```\s*$", "", raw)
        return json.loads(raw)
    except Exception as e:
        logger.error(f"AI generation failed for {slug}: {e}")
        return {}


# --- Fallback generation (no AI) ---

def generate_fallback_enrichment(
    name: str,
    slug: str,
    summary: str,
    description: str | None,
    capabilities: list[dict],
    entrypoint: str | None,
) -> dict:
    """Generate basic enrichment without AI as ultimate fallback."""
    module_path = slug.replace("-", "_") + ".tool"
    func_name = "run"
    if entrypoint and ":" in entrypoint:
        module_path, func_name = entrypoint.rsplit(":", 1)
    elif entrypoint and "." in entrypoint:
        module_path = entrypoint
        func_name = "run"

    cap_names = [c.get("name", "") for c in capabilities]

    use_cases = []
    if summary:
        # Generate basic use cases from summary
        use_cases = [summary]
        for cap in cap_names[:3]:
            readable = cap.replace("_", " ").title()
            use_cases.append(f"Use {readable} in automated workflows")

    examples = [{
        "title": f"Basic usage of {name}",
        "language": "python",
        "code": f"from {module_path} import {func_name}\n\nresult = {func_name}()\nprint(result)",
    }]

    readme = f"""# {name}

{summary}

{description or ''}

## Quick Start

```bash
agentnode install {slug}
```

```python
from {module_path} import {func_name}

result = {func_name}()
print(result)
```

## Capabilities

{chr(10).join(f'- **{c.get("name", "")}** ({c.get("capability_id", "")})' for c in capabilities)}

## License

MIT
"""

    return {
        "use_cases": use_cases,
        "examples": examples,
        "env_requirements": [],
        "readme_md": readme,
    }


# --- Main backfill ---

async def backfill():
    """Main backfill function."""
    async with engine.begin() as conn:
        # Get all package versions with their package info
        result = await conn.execute(text("""
            SELECT
                pv.id as version_id,
                pv.artifact_object_key,
                pv.readme_md,
                pv.file_list,
                pv.use_cases,
                pv.examples,
                pv.env_requirements,
                pv.entrypoint,
                pv.version_number,
                p.slug,
                p.name,
                p.summary,
                p.description,
                p.license_model
            FROM package_versions pv
            JOIN packages p ON p.id = pv.package_id
            WHERE p.latest_version_id = pv.id
            ORDER BY p.slug
        """))
        rows = result.fetchall()
        logger.info(f"Found {len(rows)} latest versions to backfill")

        for row in rows:
            version_id = str(row.version_id)
            slug = row.slug
            name = row.name
            summary = row.summary
            description = row.description
            entrypoint = row.entrypoint
            artifact_key = row.artifact_object_key
            has_artifact = bool(artifact_key)

            needs_artifact_extract = row.file_list is None and has_artifact
            needs_ai = row.use_cases is None or row.examples is None
            needs_readme = row.readme_md is None

            if not needs_artifact_extract and not needs_ai and not needs_readme:
                logger.info(f"  SKIP {slug} — already complete")
                continue

            logger.info(f"\n{'='*60}")
            logger.info(f"  Processing {slug}@{row.version_number} (id={version_id})")

            updates = {}

            # --- Step 1: Extract from artifact ---
            if needs_artifact_extract:
                try:
                    logger.info(f"  Downloading artifact: {artifact_key}")
                    artifact_bytes = await download_artifact(artifact_key)
                    metadata = await extract_artifact_metadata(artifact_bytes, version_id)

                    if metadata["file_list"]:
                        updates["file_list"] = json.dumps(metadata["file_list"])
                        logger.info(f"  Extracted file_list: {len(metadata['file_list'])} files")

                    if metadata["readme_md"]:
                        updates["readme_md"] = metadata["readme_md"]
                        needs_readme = False
                        logger.info(f"  Extracted README.md ({len(metadata['readme_md'])} chars)")

                    if metadata["preview_count"] > 0:
                        logger.info(f"  Uploaded {metadata['preview_count']} preview files")

                except Exception as e:
                    logger.warning(f"  Artifact extraction failed: {e}")

            # --- Step 2: Get capabilities + permissions for AI context ---
            caps_result = await conn.execute(text(
                "SELECT name, capability_id, capability_type, description "
                "FROM capabilities WHERE package_version_id = :vid"
            ), {"vid": version_id})
            capabilities = [dict(r._mapping) for r in caps_result.fetchall()]

            perms_result = await conn.execute(text(
                "SELECT network_level, filesystem_level, code_execution_level "
                "FROM permissions WHERE package_version_id = :vid"
            ), {"vid": version_id})
            perms_row = perms_result.fetchone()
            permissions = dict(perms_row._mapping) if perms_row else None

            # --- Step 3: AI generation ---
            if needs_ai or needs_readme:
                logger.info(f"  Generating enrichment with AI...")
                ai_data = await generate_enrichment_with_ai(
                    name=name,
                    slug=slug,
                    summary=summary,
                    description=description,
                    capabilities=capabilities,
                    entrypoint=entrypoint,
                    permissions=permissions,
                    has_readme=not needs_readme,
                )

                if not ai_data:
                    logger.info(f"  AI failed, using fallback generation")
                    ai_data = generate_fallback_enrichment(
                        name=name,
                        slug=slug,
                        summary=summary,
                        description=description,
                        capabilities=capabilities,
                        entrypoint=entrypoint,
                    )

                if ai_data.get("use_cases") and row.use_cases is None:
                    updates["use_cases"] = json.dumps(ai_data["use_cases"])
                    logger.info(f"  Generated {len(ai_data['use_cases'])} use cases")

                if ai_data.get("examples") and row.examples is None:
                    updates["examples"] = json.dumps(ai_data["examples"])
                    logger.info(f"  Generated {len(ai_data['examples'])} examples")

                if ai_data.get("env_requirements") is not None and row.env_requirements is None:
                    updates["env_requirements"] = json.dumps(ai_data["env_requirements"])
                    logger.info(f"  Generated {len(ai_data['env_requirements'])} env requirements")

                if ai_data.get("readme_md") and needs_readme:
                    updates["readme_md"] = ai_data["readme_md"]
                    logger.info(f"  Generated README ({len(ai_data['readme_md'])} chars)")

            # --- Step 4: Write to DB ---
            if updates:
                set_clauses = []
                params = {"vid": version_id}
                for key, value in updates.items():
                    set_clauses.append(f"{key} = :{key}")
                    params[key] = value

                sql = f"UPDATE package_versions SET {', '.join(set_clauses)} WHERE id = :vid"
                await conn.execute(text(sql), params)
                logger.info(f"  Updated {len(updates)} fields: {list(updates.keys())}")
            else:
                logger.info(f"  No updates needed")

            # Rate limit for AI calls
            if needs_ai or needs_readme:
                await asyncio.sleep(1)

    await engine.dispose()
    logger.info(f"\n{'='*60}")
    logger.info("Backfill complete!")


if __name__ == "__main__":
    asyncio.run(backfill())
