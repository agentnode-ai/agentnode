"""Screenshot capture tool using Playwright."""

from __future__ import annotations

import os
import tempfile


def run(
    url: str,
    output_path: str = "",
    full_page: bool = True,
    viewport_width: int = 1280,
    viewport_height: int = 720,
) -> dict:
    """Capture a screenshot of a webpage using Playwright.

    Args:
        url: The URL to screenshot.
        output_path: Where to save the PNG. Defaults to a temp file.
        full_page: Capture the full scrollable page (True) or just the viewport (False).
        viewport_width: Browser viewport width in pixels.
        viewport_height: Browser viewport height in pixels.

    Returns:
        dict with keys: output_path, width, height, url.
    """
    from playwright.sync_api import sync_playwright

    if not output_path:
        fd, output_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": viewport_width, "height": viewport_height},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        try:
            page.goto(url, wait_until="networkidle", timeout=30000)

            # Determine actual page dimensions for the response
            dimensions = page.evaluate(
                """() => ({
                    width: document.documentElement.scrollWidth,
                    height: document.documentElement.scrollHeight
                })"""
            )

            page.screenshot(path=output_path, full_page=full_page)

            width = dimensions["width"] if full_page else viewport_width
            height = dimensions["height"] if full_page else viewport_height

        finally:
            browser.close()

    return {
        "output_path": output_path,
        "width": width,
        "height": height,
        "url": url,
    }
