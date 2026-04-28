"""Tests for browser-automation-pack."""

import pytest
from unittest.mock import MagicMock, patch

from agentnode_sdk.exceptions import AgentNodeToolError
from browser_automation_pack.tool import run


# -- Input validation --

def test_unknown_action():
    with pytest.raises(AgentNodeToolError, match="Unknown action"):
        run(url="https://example.com", action="hack")


def test_click_missing_selector():
    with pytest.raises(AgentNodeToolError, match="selector is required"):
        run(url="https://example.com", action="click", selector="")


def test_fill_missing_selector():
    with pytest.raises(AgentNodeToolError, match="selector is required"):
        run(url="https://example.com", action="fill", selector="")


# -- Mocked screenshot --

@patch("playwright.sync_api.sync_playwright")
def test_screenshot(mock_pw_fn, tmp_path):
    mock_pw = MagicMock()
    mock_pw_fn.return_value.start.return_value = mock_pw

    mock_page = MagicMock()
    mock_page.url = "https://example.com"
    mock_page.title.return_value = "Example"
    mock_page.screenshot = MagicMock()

    mock_browser = MagicMock()
    mock_browser.new_context.return_value.new_page.return_value = mock_page
    mock_pw.chromium.launch.return_value = mock_browser

    out = str(tmp_path / "shot.png")
    result = run(url="https://example.com", action="screenshot", output_path=out)
    assert result["action"] == "screenshot"
    assert result["url"] == "https://example.com"
    mock_page.screenshot.assert_called_once()


# -- Mocked extract_text --

@patch("playwright.sync_api.sync_playwright")
def test_extract_text(mock_pw_fn):
    mock_pw = MagicMock()
    mock_pw_fn.return_value.start.return_value = mock_pw

    mock_page = MagicMock()
    mock_page.url = "https://example.com"
    mock_page.title.return_value = "Example"
    mock_page.inner_text.return_value = "Page content here"

    mock_browser = MagicMock()
    mock_browser.new_context.return_value.new_page.return_value = mock_page
    mock_pw.chromium.launch.return_value = mock_browser

    result = run(url="https://example.com", action="extract_text")
    assert result["action"] == "extract_text"
    assert result["text"] == "Page content here"


# -- Mocked get_links --

@patch("playwright.sync_api.sync_playwright")
def test_get_links(mock_pw_fn):
    mock_pw = MagicMock()
    mock_pw_fn.return_value.start.return_value = mock_pw

    mock_link = MagicMock()
    mock_link.get_attribute.return_value = "https://example.com/page"
    mock_link.inner_text.return_value = "Page Link"

    mock_page = MagicMock()
    mock_page.url = "https://example.com"
    mock_page.query_selector_all.return_value = [mock_link]

    mock_browser = MagicMock()
    mock_browser.new_context.return_value.new_page.return_value = mock_page
    mock_pw.chromium.launch.return_value = mock_browser

    result = run(url="https://example.com", action="get_links")
    assert result["action"] == "get_links"
    assert result["total"] == 1
    assert result["links"][0]["href"] == "https://example.com/page"
