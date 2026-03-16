"""Extract text, tables, and optionally images from PDF files."""

from __future__ import annotations

import io
import os
from pathlib import Path

import pdfplumber
from PIL import Image


def _parse_page_range(pages: str, total: int) -> list[int]:
    """Parse a page range string into a list of 0-based page indices."""
    if pages.strip().lower() == "all":
        return list(range(total))

    indices: list[int] = []
    for part in pages.split(","):
        part = part.strip()
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start = max(int(start_s.strip()) - 1, 0)
            end = min(int(end_s.strip()), total)
            indices.extend(range(start, end))
        else:
            idx = int(part.strip()) - 1
            if 0 <= idx < total:
                indices.append(idx)
    return sorted(set(indices))


def run(
    file_path: str,
    pages: str = "all",
    extract_tables: bool = True,
    extract_images: bool = False,
) -> dict:
    """Extract text, tables, and optionally images from a PDF file.

    Args:
        file_path: Path to the PDF file.
        pages: Page range to extract (e.g. "all", "1-3", "1,3,5").
        extract_tables: Whether to extract tables from the PDF.
        extract_images: Whether to extract images from the PDF.

    Returns:
        Dictionary with text, tables, images, page_count, and metadata.
    """
    file_path = os.path.abspath(file_path)
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"PDF file not found: {file_path}")

    all_text: list[str] = []
    all_tables: list[dict] = []
    all_images: list[dict] = []

    with pdfplumber.open(file_path) as pdf:
        total_pages = len(pdf.pages)
        metadata = pdf.metadata or {}
        page_indices = _parse_page_range(pages, total_pages)

        for idx in page_indices:
            page = pdf.pages[idx]
            page_num = idx + 1

            # --- Text extraction ---
            text = page.extract_text() or ""
            if text:
                all_text.append(text)

            # --- Table extraction ---
            if extract_tables:
                tables = page.extract_tables() or []
                for ti, table in enumerate(tables):
                    if not table:
                        continue
                    header = table[0] if table else []
                    rows = table[1:] if len(table) > 1 else []
                    all_tables.append(
                        {
                            "page": page_num,
                            "table_index": ti,
                            "header": header,
                            "rows": rows,
                            "row_count": len(rows),
                        }
                    )

            # --- Image extraction ---
            if extract_images:
                page_images = page.images or []
                for ii, img_info in enumerate(page_images):
                    try:
                        x0 = img_info["x0"]
                        top = img_info["top"]
                        x1 = img_info["x1"]
                        bottom = img_info["bottom"]
                        cropped = page.within_bbox((x0, top, x1, bottom)).to_image(
                            resolution=150
                        )
                        buf = io.BytesIO()
                        cropped.save(buf, format="PNG")
                        # Store base64-encoded image
                        import base64

                        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
                        all_images.append(
                            {
                                "page": page_num,
                                "image_index": ii,
                                "width": int(x1 - x0),
                                "height": int(bottom - top),
                                "format": "png",
                                "data_base64": encoded,
                            }
                        )
                    except Exception:
                        # Skip images that cannot be extracted
                        pass

    # Sanitise metadata values to strings
    clean_metadata = {}
    for k, v in metadata.items():
        if v is not None:
            clean_metadata[str(k)] = str(v)

    return {
        "text": "\n\n".join(all_text),
        "tables": all_tables,
        "images": all_images,
        "page_count": total_pages,
        "metadata": clean_metadata,
    }
