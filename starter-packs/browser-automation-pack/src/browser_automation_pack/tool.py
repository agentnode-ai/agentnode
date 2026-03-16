"""Browser automation tool using Playwright sync API."""

from __future__ import annotations

import tempfile
import os


def _screenshot(page, output_path: str, **kwargs) -> dict:
    """Capture a screenshot of the current page."""
    full_page = kwargs.get("full_page", True)
    if not output_path:
        fd, output_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
    page.screenshot(path=output_path, full_page=full_page)
    return {
        "action": "screenshot",
        "output_path": output_path,
        "url": page.url,
        "title": page.title(),
    }


def _extract_text(page, selector: str, **kwargs) -> dict:
    """Extract text content from the page or a specific selector."""
    if selector:
        element = page.query_selector(selector)
        if element is None:
            return {"action": "extract_text", "error": f"Selector '{selector}' not found", "text": ""}
        text = element.inner_text()
    else:
        text = page.inner_text("body")
    return {
        "action": "extract_text",
        "text": text,
        "url": page.url,
        "title": page.title(),
    }


def _click(page, selector: str, **kwargs) -> dict:
    """Click an element on the page."""
    if not selector:
        return {"action": "click", "error": "A selector is required for the click action"}
    page.click(selector)
    page.wait_for_load_state("networkidle", timeout=10000)
    return {
        "action": "click",
        "selector": selector,
        "url": page.url,
        "title": page.title(),
    }


def _fill(page, selector: str, **kwargs) -> dict:
    """Fill in a form field."""
    value = kwargs.get("value", "")
    if not selector:
        return {"action": "fill", "error": "A selector is required for the fill action"}
    page.fill(selector, value)
    return {
        "action": "fill",
        "selector": selector,
        "value": value,
        "url": page.url,
    }


def _get_links(page, selector: str, **kwargs) -> dict:
    """Extract all links from the page or a scoped container."""
    scope = selector if selector else "body"
    elements = page.query_selector_all(f"{scope} a[href]")
    links: list[dict[str, str]] = []
    for el in elements:
        href = el.get_attribute("href") or ""
        text = el.inner_text().strip()
        links.append({"href": href, "text": text})
    return {
        "action": "get_links",
        "links": links,
        "total": len(links),
        "url": page.url,
    }


_ACTIONS = {
    "screenshot": _screenshot,
    "extract_text": _extract_text,
    "click": _click,
    "fill": _fill,
    "get_links": _get_links,
}


def run(
    url: str,
    action: str = "screenshot",
    selector: str = "",
    output_path: str = "",
    **kwargs,
) -> dict:
    """Automate browser interactions with Playwright.

    Args:
        url: The URL to navigate to.
        action: One of 'screenshot', 'extract_text', 'click', 'fill', 'get_links'.
        selector: CSS selector for targeted actions.
        output_path: File path for screenshot output.
        **kwargs: Extra arguments (e.g. value for fill, full_page for screenshot).

    Returns:
        dict with action-specific results.
    """
    from playwright.sync_api import sync_playwright

    action = action.lower().strip()
    if action not in _ACTIONS:
        return {"error": f"Unknown action: {action}. Supported: {', '.join(_ACTIONS)}"}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": kwargs.get("viewport_width", 1280), "height": kwargs.get("viewport_height", 720)},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            result = _ACTIONS[action](page, selector=selector, output_path=output_path, **kwargs)
        except Exception as exc:
            result = {"error": str(exc), "action": action, "url": url}
        finally:
            browser.close()

    return result
