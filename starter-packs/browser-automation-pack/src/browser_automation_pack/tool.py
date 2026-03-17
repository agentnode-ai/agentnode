"""Browser automation tool using Playwright sync API. ANP v0.2 — per-tool entrypoints."""

from __future__ import annotations

import os
import tempfile
from typing import Any

from agentnode_sdk.exceptions import AgentNodeToolError


def _launch_and_navigate(url: str, **kwargs):
    """Shared helper: launch browser, navigate, return (browser, page) context manager."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={
            "width": kwargs.get("viewport_width", 1280),
            "height": kwargs.get("viewport_height", 720),
        },
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    page = context.new_page()
    page.goto(url, wait_until="networkidle", timeout=30000)
    return pw, browser, page


def _cleanup(pw, browser):
    browser.close()
    pw.stop()


def screenshot(url: str, output_path: str = "", full_page: bool = True) -> dict:
    """Capture a screenshot of a web page.

    Args:
        url: The URL to navigate to.
        output_path: File path for screenshot output. Auto-generated if empty.
        full_page: Whether to capture the full scrollable page.

    Returns:
        A dict with the output path, URL, and page title.
    """
    pw, browser, page = _launch_and_navigate(url)
    try:
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
    except Exception as e:
        raise AgentNodeToolError(str(e), tool_name="screenshot")
    finally:
        _cleanup(pw, browser)


def extract_text(url: str, selector: str = "") -> dict:
    """Extract text content from a web page.

    Args:
        url: The URL to navigate to.
        selector: CSS selector for targeted extraction. Empty = full body text.

    Returns:
        A dict with the extracted text, URL, and title.
    """
    pw, browser, page = _launch_and_navigate(url)
    try:
        if selector:
            element = page.query_selector(selector)
            if element is None:
                raise AgentNodeToolError(
                    f"Selector '{selector}' not found on page",
                    tool_name="extract_text",
                )
            text = element.inner_text()
        else:
            text = page.inner_text("body")
        return {
            "action": "extract_text",
            "text": text,
            "url": page.url,
            "title": page.title(),
        }
    except AgentNodeToolError:
        raise
    except Exception as e:
        raise AgentNodeToolError(str(e), tool_name="extract_text")
    finally:
        _cleanup(pw, browser)


def click(url: str, selector: str) -> dict:
    """Click an element on a web page.

    Args:
        url: The URL to navigate to.
        selector: CSS selector of the element to click.

    Returns:
        A dict with the click result, URL, and title after navigation.
    """
    if not selector:
        raise AgentNodeToolError("A selector is required for click", tool_name="click")

    pw, browser, page = _launch_and_navigate(url)
    try:
        page.click(selector)
        page.wait_for_load_state("networkidle", timeout=10000)
        return {
            "action": "click",
            "selector": selector,
            "url": page.url,
            "title": page.title(),
        }
    except Exception as e:
        raise AgentNodeToolError(str(e), tool_name="click")
    finally:
        _cleanup(pw, browser)


def fill(url: str, selector: str, value: str = "") -> dict:
    """Fill a form field on a web page.

    Args:
        url: The URL to navigate to.
        selector: CSS selector of the input field.
        value: The value to type into the field.

    Returns:
        A dict with the fill result.
    """
    if not selector:
        raise AgentNodeToolError("A selector is required for fill", tool_name="fill")

    pw, browser, page = _launch_and_navigate(url)
    try:
        page.fill(selector, value)
        return {
            "action": "fill",
            "selector": selector,
            "value": value,
            "url": page.url,
        }
    except Exception as e:
        raise AgentNodeToolError(str(e), tool_name="fill")
    finally:
        _cleanup(pw, browser)


def get_links(url: str, selector: str = "") -> dict:
    """Extract all links from a web page.

    Args:
        url: The URL to navigate to.
        selector: Optional CSS selector to scope the link search.

    Returns:
        A dict with a list of links (href + text).
    """
    pw, browser, page = _launch_and_navigate(url)
    try:
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
    except Exception as e:
        raise AgentNodeToolError(str(e), tool_name="get_links")
    finally:
        _cleanup(pw, browser)


# Backward-compatible v0.1 entrypoint
def run(
    url: str,
    action: str = "screenshot",
    selector: str = "",
    output_path: str = "",
    **kwargs,
) -> dict:
    """Automate browser interactions with Playwright (v0.1 compatibility wrapper).

    Args:
        url: The URL to navigate to.
        action: One of 'screenshot', 'extract_text', 'click', 'fill', 'get_links'.
        selector: CSS selector for targeted actions.
        output_path: File path for screenshot output.
        **kwargs: Extra arguments.
    """
    dispatch = {
        "screenshot": lambda: screenshot(url, output_path=output_path, full_page=kwargs.get("full_page", True)),
        "extract_text": lambda: extract_text(url, selector=selector),
        "click": lambda: click(url, selector=selector),
        "fill": lambda: fill(url, selector=selector, value=kwargs.get("value", "")),
        "get_links": lambda: get_links(url, selector=selector),
    }
    handler = dispatch.get(action.lower().strip())
    if not handler:
        raise AgentNodeToolError(
            f"Unknown action: {action}. Supported: {', '.join(dispatch)}",
            tool_name="browser_navigation",
        )
    return handler()
