"""Capability relationship graph with typed weighted edges.

How capabilities enter the graph:

1. A package declares ``capability_ids`` in its manifest (agentnode.yaml).
2. This graph maps relationships between those capability IDs.
3. ``recommend``, ``doctor``, and ``resolve`` use the graph to prioritize
   results and detect gaps.
4. Unknown capability IDs are allowed — they simply have no graph edges,
   so they receive no boost or gap signal.

Edge types:
- ``complements``: both capabilities are more useful together.
- ``requires``: this capability is broken/useless without the target.
  Use sparingly — most capabilities work standalone.
- ``enhances``: target makes this capability better but is not needed.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Edge:
    target: str
    relationship: str  # "complements", "requires", "enhances"
    weight: float      # 0.0–1.0


CAPABILITY_GRAPH: dict[str, list[Edge]] = {
    # --- Search & Web ---
    "web_search": [
        Edge("text_summarization", "complements", 0.8),
        Edge("webpage_extraction", "complements", 0.9),
        Edge("text_translation", "enhances", 0.4),
        Edge("knowledge_graph", "enhances", 0.3),
    ],
    "webpage_extraction": [
        Edge("web_search", "complements", 0.7),
        Edge("text_summarization", "complements", 0.8),
        Edge("pdf_extraction", "enhances", 0.5),
    ],
    "browser_navigation": [
        Edge("webpage_extraction", "complements", 0.8),
        Edge("web_search", "complements", 0.6),
        Edge("screenshot_capture", "complements", 0.7),
    ],

    # --- Document Processing ---
    "pdf_extraction": [
        Edge("text_summarization", "complements", 0.9),
        Edge("ocr_reading", "complements", 0.7),
        Edge("document_parsing", "enhances", 0.6),
    ],
    "document_parsing": [
        Edge("pdf_extraction", "complements", 0.7),
        Edge("text_summarization", "complements", 0.6),
        Edge("spreadsheet_parsing", "enhances", 0.4),
    ],
    "ocr_reading": [
        Edge("pdf_extraction", "complements", 0.8),
        Edge("text_summarization", "enhances", 0.5),
        Edge("document_parsing", "enhances", 0.4),
    ],

    # --- Text Processing ---
    "text_summarization": [
        Edge("text_translation", "complements", 0.6),
        Edge("web_search", "enhances", 0.5),
        Edge("pdf_extraction", "enhances", 0.5),
    ],
    "text_translation": [
        Edge("text_summarization", "complements", 0.6),
        Edge("language_detection", "enhances", 0.5),
    ],
    "language_detection": [
        Edge("text_translation", "complements", 0.9),
        Edge("text_summarization", "enhances", 0.3),
    ],

    # --- Data & Analytics ---
    "csv_analysis": [
        Edge("chart_generation", "complements", 0.9),
        Edge("data_visualization", "complements", 0.8),
        Edge("spreadsheet_parsing", "enhances", 0.6),
        Edge("sql_generation", "enhances", 0.5),
    ],
    "spreadsheet_parsing": [
        Edge("csv_analysis", "complements", 0.7),
        Edge("chart_generation", "enhances", 0.5),
    ],
    "chart_generation": [
        Edge("csv_analysis", "complements", 0.8),
        Edge("data_visualization", "complements", 0.7),
    ],
    "data_visualization": [
        Edge("csv_analysis", "complements", 0.7),
        Edge("chart_generation", "complements", 0.8),
    ],
    "sql_generation": [
        Edge("csv_analysis", "complements", 0.6),
        Edge("database_connector", "complements", 0.7),
    ],
    "database_connector": [
        Edge("sql_generation", "complements", 0.8),
        Edge("csv_analysis", "enhances", 0.4),
    ],

    # --- AI / Embeddings ---
    "embedding_generation": [
        Edge("vector_memory", "complements", 0.7),
        Edge("text_summarization", "enhances", 0.4),
    ],
    "vector_memory": [
        Edge("embedding_generation", "requires", 0.9),
        Edge("web_search", "enhances", 0.5),
    ],

    # --- Code ---
    "code_analysis": [
        Edge("code_generation", "complements", 0.8),
        Edge("test_generation", "complements", 0.7),
    ],
    "code_generation": [
        Edge("code_analysis", "complements", 0.7),
        Edge("test_generation", "complements", 0.8),
    ],
    "test_generation": [
        Edge("code_analysis", "complements", 0.6),
        Edge("code_generation", "complements", 0.7),
    ],

    # --- Communication ---
    "email_sending": [
        Edge("email_reading", "complements", 0.8),
        Edge("text_summarization", "enhances", 0.4),
    ],
    "email_reading": [
        Edge("email_sending", "complements", 0.8),
        Edge("text_summarization", "enhances", 0.5),
    ],

    # --- Media ---
    "screenshot_capture": [
        Edge("browser_navigation", "complements", 0.7),
        Edge("ocr_reading", "enhances", 0.5),
    ],
    "image_generation": [
        Edge("image_editing", "complements", 0.7),
    ],
    "image_editing": [
        Edge("image_generation", "complements", 0.6),
        Edge("ocr_reading", "enhances", 0.3),
    ],
}


def neighbors(cap: str, min_weight: float = 0.0) -> list[Edge]:
    """Direct neighbors of a capability, filtered by minimum weight."""
    return [e for e in CAPABILITY_GRAPH.get(cap, []) if e.weight >= min_weight]


def available_capabilities() -> set[str]:
    """Capabilities that currently have installable packages.

    Sources: taxonomy ``active`` status + installed packages' capability_ids.
    Installed packages may declare capabilities not yet in the taxonomy.
    """
    from agentnode_sdk.capability_taxonomy import CAPABILITY_TAXONOMY
    from agentnode_sdk.installer import read_lockfile

    active = {cap for cap, meta in CAPABILITY_TAXONOMY.items() if meta["status"] == "active"}

    try:
        lock = read_lockfile()
        for info in lock.get("packages", {}).values():
            for cap_id in info.get("capability_ids", []):
                active.add(cap_id)
    except Exception:
        pass

    return active


def missing_for(
    installed: set[str],
    min_weight: float = 0.3,
) -> list[tuple[str, float, str]]:
    """Find missing capabilities ranked by priority.

    Returns [(capability, priority_score, reason)] sorted by priority desc.
    Priority accumulates across all installed capabilities that point to it.
    Only returns capabilities that are available at runtime (not planned).
    """
    available = available_capabilities()
    scores: dict[str, float] = {}
    reasons: dict[str, list[str]] = {}

    for cap in installed:
        for edge in CAPABILITY_GRAPH.get(cap, []):
            if edge.target in installed:
                continue
            if edge.weight < min_weight:
                continue
            scores[edge.target] = scores.get(edge.target, 0.0) + edge.weight
            verb = {"complements": "Complements", "requires": "Required by", "enhances": "Enhances"}.get(
                edge.relationship, "Related to"
            )
            reasons.setdefault(edge.target, []).append(f"{verb} {cap} ({edge.weight:.1f})")

    result = []
    for target, score in scores.items():
        if target not in available:
            continue
        reason = ", ".join(reasons[target])
        result.append((target, round(score, 2), reason))

    result.sort(key=lambda x: x[1], reverse=True)
    return result


def priority_label(score: float) -> str:
    """Map a priority score to a human label."""
    if score >= 0.7:
        return "high"
    if score >= 0.3:
        return "suggested"
    return "low"
