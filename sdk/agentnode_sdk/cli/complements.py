"""Capability complement graph — shared between doctor and recommend.

Backward-compatible dict derived from the canonical capability_graph.
"""
from __future__ import annotations

from agentnode_sdk.capability_graph import CAPABILITY_GRAPH

CAPABILITY_COMPLEMENTS: dict[str, list[str]] = {
    cap: [edge.target for edge in edges]
    for cap, edges in CAPABILITY_GRAPH.items()
}
