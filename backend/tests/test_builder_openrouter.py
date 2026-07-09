"""Builder LLM provider dispatch: OpenRouter preferred, Anthropic fallback."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.builder.ai import generate_skill_with_ai
from app.config import settings

SKILL_JSON = {
    "package_id": "release-notes-writer",
    "name": "Release Notes Writer",
    "summary": "Turn commits into release notes.",
    "description": "A prompt-only skill for release notes.",
    "skill_md": "# Release Notes Writer\n\nWrite for the user.",
    "use_cases": ["Draft release notes"],
    "warnings": [],
}


def _openrouter_response(content: str):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}]
    }
    return resp


def _mock_httpx_client(response):
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.mark.asyncio
async def test_openrouter_preferred_when_key_set(monkeypatch):
    """With an OpenRouter key set, generation goes through OpenRouter."""
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(settings, "BUILDER_MODEL", "test/model")

    client = _mock_httpx_client(_openrouter_response(json.dumps(SKILL_JSON)))
    with patch("httpx.AsyncClient", return_value=client):
        result = await generate_skill_with_ai("write release notes")

    assert result.manifest_json["package_id"] == "release-notes-writer"
    assert result.code_files[0].path == "SKILL.md"

    call = client.post.call_args
    assert "openrouter.ai" in call.args[0]
    sent = call.kwargs["json"]
    assert sent["model"] == "test/model"
    assert sent["messages"][0]["role"] == "system"


@pytest.mark.asyncio
async def test_anthropic_fallback_without_openrouter_key(monkeypatch):
    """Without an OpenRouter key, the Anthropic path is used."""
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test")

    message = MagicMock()
    message.content = [MagicMock(text=json.dumps(SKILL_JSON))]
    message.stop_reason = "end_turn"
    anthropic_client = MagicMock()
    anthropic_client.messages.create = AsyncMock(return_value=message)

    with patch("anthropic.AsyncAnthropic", return_value=anthropic_client):
        result = await generate_skill_with_ai("write release notes")

    assert result.manifest_json["package_id"] == "release-notes-writer"
    anthropic_client.messages.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_openrouter_http_error_raises(monkeypatch):
    """Provider errors bubble up so the router can fall back to heuristic."""
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-or-test")

    resp = MagicMock()
    resp.raise_for_status.side_effect = Exception("HTTP 402")
    client = _mock_httpx_client(resp)
    with patch("httpx.AsyncClient", return_value=client):
        with pytest.raises(Exception, match="402"):
            await generate_skill_with_ai("write release notes")
