"""Convert files between formats: md->html, html->md, csv->json, json->csv, txt->md."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
from html.parser import HTMLParser
from pathlib import Path

import markdown as md_lib


# ---------------------------------------------------------------------------
# HTML -> Markdown converter (stdlib html.parser)
# ---------------------------------------------------------------------------

class _HTMLToMarkdown(HTMLParser):
    """Minimal HTML-to-Markdown converter using the stdlib HTMLParser."""

    def __init__(self) -> None:
        super().__init__()
        self._result: list[str] = []
        self._tag_stack: list[str] = []
        self._list_stack: list[str] = []  # "ul" or "ol"
        self._ol_counter: list[int] = []

    # -- helpers ----------------------------------------------------------

    def _current_tag(self) -> str | None:
        return self._tag_stack[-1] if self._tag_stack else None

    # -- handle_* overrides -----------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self._tag_stack.append(tag)
        attrs_dict = dict(attrs)

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            self._result.append("\n" + "#" * level + " ")
        elif tag == "p":
            self._result.append("\n\n")
        elif tag == "br":
            self._result.append("  \n")
        elif tag == "strong" or tag == "b":
            self._result.append("**")
        elif tag == "em" or tag == "i":
            self._result.append("*")
        elif tag == "code":
            self._result.append("`")
        elif tag == "a":
            self._result.append("[")
        elif tag == "ul":
            self._list_stack.append("ul")
            self._result.append("\n")
        elif tag == "ol":
            self._list_stack.append("ol")
            self._ol_counter.append(0)
            self._result.append("\n")
        elif tag == "li":
            indent = "  " * max(0, len(self._list_stack) - 1)
            if self._list_stack and self._list_stack[-1] == "ol":
                self._ol_counter[-1] += 1
                self._result.append(f"{indent}{self._ol_counter[-1]}. ")
            else:
                self._result.append(f"{indent}- ")
        elif tag == "blockquote":
            self._result.append("\n> ")
        elif tag == "pre":
            self._result.append("\n```\n")
        elif tag == "hr":
            self._result.append("\n---\n")
        elif tag == "img":
            alt = attrs_dict.get("alt", "")
            src = attrs_dict.get("src", "")
            self._result.append(f"![{alt}]({src})")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._result.append("\n")
        elif tag in ("strong", "b"):
            self._result.append("**")
        elif tag in ("em", "i"):
            self._result.append("*")
        elif tag == "code":
            self._result.append("`")
        elif tag == "a":
            self._result.append("]()")  # href would need attrs tracking
        elif tag == "li":
            self._result.append("\n")
        elif tag == "ul":
            if self._list_stack:
                self._list_stack.pop()
        elif tag == "ol":
            if self._list_stack:
                self._list_stack.pop()
            if self._ol_counter:
                self._ol_counter.pop()
        elif tag == "pre":
            self._result.append("\n```\n")
        elif tag == "p":
            self._result.append("\n")

    def handle_data(self, data: str) -> None:
        self._result.append(data)

    def get_markdown(self) -> str:
        text = "".join(self._result)
        # Collapse excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip() + "\n"


# ---------------------------------------------------------------------------
# Conversion functions
# ---------------------------------------------------------------------------

def _md_to_html(source: str) -> str:
    return md_lib.markdown(source, extensions=["tables", "fenced_code"])


def _html_to_md(source: str) -> str:
    parser = _HTMLToMarkdown()
    parser.feed(source)
    return parser.get_markdown()


def _csv_to_json(source: str) -> str:
    reader = csv.DictReader(io.StringIO(source))
    rows = list(reader)
    return json.dumps(rows, indent=2, ensure_ascii=False)


def _json_to_csv(source: str) -> str:
    data = json.loads(source)
    if not isinstance(data, list) or not data:
        raise ValueError("JSON input must be a non-empty array of objects.")

    # Gather all keys to form the header
    fieldnames: list[str] = []
    seen: set[str] = set()
    for item in data:
        if isinstance(item, dict):
            for k in item:
                if k not in seen:
                    fieldnames.append(k)
                    seen.add(k)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for item in data:
        if isinstance(item, dict):
            writer.writerow(item)
    return buf.getvalue()


def _txt_to_md(source: str) -> str:
    """Convert plain text to Markdown by wrapping paragraphs."""
    paragraphs = re.split(r"\n{2,}", source.strip())
    return "\n\n".join(p.strip() for p in paragraphs if p.strip()) + "\n"


_CONVERTERS: dict[tuple[str, str], callable] = {
    ("md", "html"): _md_to_html,
    ("html", "md"): _html_to_md,
    ("csv", "json"): _csv_to_json,
    ("json", "csv"): _json_to_csv,
    ("txt", "md"): _txt_to_md,
}

_EXT_MAP = {
    "md": ".md",
    "html": ".html",
    "csv": ".csv",
    "json": ".json",
    "txt": ".txt",
}


def _detect_format(path: str) -> str:
    ext = Path(path).suffix.lower().lstrip(".")
    if ext in ("htm",):
        return "html"
    if ext == "markdown":
        return "md"
    return ext


def run(
    input_path: str,
    output_format: str,
    output_path: str = "",
) -> dict:
    """Convert a file to a different format.

    Supported conversions: md->html, html->md, csv->json, json->csv, txt->md.

    Args:
        input_path: Path to the source file.
        output_format: Target format (e.g. "html", "md", "json", "csv").
        output_path: Destination path. Auto-generated if empty.

    Returns:
        Dictionary with output_path, input_format, and output_format.
    """
    input_path = os.path.abspath(input_path)
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    input_format = _detect_format(input_path)
    output_format = output_format.strip().lower()

    key = (input_format, output_format)
    converter = _CONVERTERS.get(key)
    if converter is None:
        supported = ", ".join(f"{a}->{b}" for a, b in _CONVERTERS)
        raise ValueError(
            f"Conversion '{input_format}' -> '{output_format}' is not supported. "
            f"Supported: {supported}"
        )

    with open(input_path, "r", encoding="utf-8") as f:
        source = f.read()

    result = converter(source)

    if not output_path:
        stem = Path(input_path).stem
        ext = _EXT_MAP.get(output_format, f".{output_format}")
        output_path = os.path.join(
            tempfile.gettempdir(), f"{stem}{ext}"
        )

    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(result)

    return {
        "output_path": output_path,
        "input_format": input_format,
        "output_format": output_format,
    }
