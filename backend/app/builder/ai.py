"""AI-powered skill generator using Claude.
Generates ANP skill packages (manifest + SKILL.md) from natural-language
descriptions. The builder is skills-only: it writes instruction documents,
never executable code."""

from __future__ import annotations

import json
import logging
import re

import anthropic

from app.builder.schemas import BuilderGenerateResponse, BuilderMetadata, CodeFile
from app.builder.service import (
    _slugify,
    build_skill_manifest,
    skill_manifest_yaml,
)
from app.config import settings

logger = logging.getLogger(__name__)

SKILL_SYSTEM_PROMPT = """\
You are the AgentNode Skill Builder — an expert at writing agent skills in ANP format.

A skill is a prompt-only package: a SKILL.md document with clear, reusable
instructions that an AI agent loads to gain a capability. Skills contain NO
executable code — no scripts, no tool definitions, no dependencies.

Given a user's description, generate package metadata and a complete,
high-quality SKILL.md instruction document.

SKILL.md QUALITY RULES:
- Write instructions FOR an AI agent (imperative voice, addressed to the agent)
- Structure: # Title, a short intro stating when to use the skill,
  "## Instructions" (numbered steps), "## Guidelines" (edge cases, tone,
  constraints), "## Output format" (with a concrete example)
- Use {{placeholder}} syntax for inputs the agent should substitute
  (e.g. {{topic}}, {{input}})
- Be specific and actionable — no filler sentences

METADATA RULES:
- package_id: lowercase kebab-case, max 40 characters
- package_name: concise, 2-4 words
- summary: one sentence
- description: 2-4 sentences
- use_cases: 3-5 strings, each "verb + concrete object"

RESPONSE FORMAT — respond with ONLY a JSON object, no markdown, no explanation:
{
  "package_id": "...",
  "package_name": "...",
  "summary": "...",
  "description": "...",
  "skill_md": "... full SKILL.md content ...",
  "use_cases": ["...", "..."],
  "warnings": []
}"""

USER_TEMPLATE = """\
Create an ANP skill for:

{description}

The SKILL.md must be complete and immediately usable by an AI agent — \
real instructions, not a scaffold."""


def _extract_json(text: str) -> dict:
    """Extract JSON from Claude's response, handling possible markdown wrapping."""
    text = text.strip()
    # Remove markdown code fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    return json.loads(text)


def _response_from_skill_data(data: dict) -> BuilderGenerateResponse:
    """Build the generate response from parsed AI output (pure, testable)."""
    package_name = (data.get("package_name") or "Custom Skill").strip()
    package_id = _slugify(data.get("package_id") or package_name) or "custom-skill"
    summary = (data.get("summary") or "").strip()
    description = (data.get("description") or summary).strip()
    skill_md = data.get("skill_md") or ""
    if not skill_md.strip():
        raise ValueError("AI response contained no skill_md content")

    use_cases = [u for u in data.get("use_cases", []) if isinstance(u, str)]
    manifest = build_skill_manifest(
        package_id=package_id,
        name=package_name,
        summary=summary,
        description=description,
        use_cases=use_cases or None,
    )

    metadata = BuilderMetadata(
        package_id=package_id,
        package_name=package_name,
        tool_count=0,
        detected_capability_ids=[],
        detected_framework="generic",
        publish_ready=True,
        warnings=[w for w in data.get("warnings", []) if isinstance(w, str)],
    )

    return BuilderGenerateResponse(
        manifest_yaml=skill_manifest_yaml(manifest),
        manifest_json=manifest,
        code_files=[CodeFile(path="SKILL.md", content=skill_md)],
        metadata=metadata,
    )


async def generate_skill_with_ai(description: str) -> BuilderGenerateResponse:
    """Generate a complete ANP skill package via the configured LLM provider.

    OpenRouter is preferred when its key is set (configurable cheap model,
    one key shared with the compatibility pipeline); Anthropic-direct is the
    fallback. The router only calls this when at least one key is configured.
    """
    if settings.OPENROUTER_API_KEY:
        raw = await _complete_via_openrouter(description)
    else:
        raw = await _complete_via_anthropic(description)

    data = _extract_json(raw)
    return _response_from_skill_data(data)


async def _complete_via_openrouter(description: str) -> str:
    """Chat completion against OpenRouter (OpenAI-compatible API)."""
    import httpx

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "HTTP-Referer": "https://agentnode.net",
                "X-Title": "AgentNode Skill Builder",
            },
            json={
                "model": settings.BUILDER_MODEL,
                "max_tokens": 8192,
                "messages": [
                    {"role": "system", "content": SKILL_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": USER_TEMPLATE.format(description=description),
                    },
                ],
            },
        )
    resp.raise_for_status()
    body = resp.json()
    raw = body["choices"][0]["message"]["content"] or ""
    logger.info(
        "AI response (openrouter/%s): %d chars, finish_reason: %s",
        settings.BUILDER_MODEL,
        len(raw),
        body["choices"][0].get("finish_reason"),
    )
    return raw


async def _complete_via_anthropic(description: str) -> str:
    """Chat completion against the Anthropic API (legacy direct path)."""
    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    message = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8192,
        system=SKILL_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": USER_TEMPLATE.format(description=description)},
        ],
    )

    raw = message.content[0].text
    logger.info(
        "AI response length: %d chars, stop_reason: %s", len(raw), message.stop_reason
    )
    return raw
