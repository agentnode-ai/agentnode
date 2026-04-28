"""Tests for screenshot-capture-pack."""

from unittest.mock import MagicMock, patch


# -- Mocked run --

@patch("playwright.sync_api.sync_playwright")
def test_screenshot_success(mock_pw_fn, tmp_path):
    mock_pw = MagicMock()
    mock_pw_fn.return_value.__enter__ = MagicMock(return_value=mock_pw)
    mock_pw_fn.return_value.__exit__ = MagicMock(return_value=False)

    mock_page = MagicMock()
    mock_page.evaluate.return_value = {"width": 1280, "height": 2400}
    mock_page.screenshot = MagicMock()

    mock_browser = MagicMock()
    mock_browser.new_context.return_value.new_page.return_value = mock_page
    mock_pw.chromium.launch.return_value = mock_browser

    out = str(tmp_path / "screenshot.png")
    from screenshot_capture_pack.tool import run
    result = run(url="https://example.com", output_path=out)
    assert result["url"] == "https://example.com"
    assert result["width"] == 1280
    assert result["height"] == 2400
    mock_page.screenshot.assert_called_once()
    mock_page.goto.assert_called_once()


@patch("playwright.sync_api.sync_playwright")
def test_viewport_only(mock_pw_fn, tmp_path):
    mock_pw = MagicMock()
    mock_pw_fn.return_value.__enter__ = MagicMock(return_value=mock_pw)
    mock_pw_fn.return_value.__exit__ = MagicMock(return_value=False)

    mock_page = MagicMock()
    mock_page.evaluate.return_value = {"width": 800, "height": 5000}
    mock_page.screenshot = MagicMock()

    mock_browser = MagicMock()
    mock_browser.new_context.return_value.new_page.return_value = mock_page
    mock_pw.chromium.launch.return_value = mock_browser

    from screenshot_capture_pack.tool import run
    result = run(url="https://example.com", output_path=str(tmp_path / "vp.png"),
                 full_page=False, viewport_width=800, viewport_height=600)
    assert result["width"] == 800
    assert result["height"] == 600


@patch("playwright.sync_api.sync_playwright")
def test_auto_output_path(mock_pw_fn):
    mock_pw = MagicMock()
    mock_pw_fn.return_value.__enter__ = MagicMock(return_value=mock_pw)
    mock_pw_fn.return_value.__exit__ = MagicMock(return_value=False)

    mock_page = MagicMock()
    mock_page.evaluate.return_value = {"width": 1280, "height": 720}
    mock_page.screenshot = MagicMock()

    mock_browser = MagicMock()
    mock_browser.new_context.return_value.new_page.return_value = mock_page
    mock_pw.chromium.launch.return_value = mock_browser

    from screenshot_capture_pack.tool import run
    result = run(url="https://example.com", output_path="")
    assert result["output_path"].endswith(".png")
