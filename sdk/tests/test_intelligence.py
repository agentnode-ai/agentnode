"""Tests for Phase 6: Intelligence Layer 2.0."""
import json
from dataclasses import field
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    lock_file = tmp_path / "agentnode.lock"
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lock_file))
    monkeypatch.delenv("AGENTNODE_NON_INTERACTIVE", raising=False)
    return tmp_path


# --- Capability Graph ---


def test_graph_neighbors():
    from agentnode_sdk.capability_graph import neighbors

    edges = neighbors("web_search")
    targets = [e.target for e in edges]
    assert "text_summarization" in targets
    assert "webpage_extraction" in targets


def test_graph_neighbors_min_weight():
    from agentnode_sdk.capability_graph import neighbors

    all_edges = neighbors("web_search", min_weight=0.0)
    strong_edges = neighbors("web_search", min_weight=0.7)
    assert len(strong_edges) <= len(all_edges)
    for e in strong_edges:
        assert e.weight >= 0.7


def test_graph_missing_for():
    from agentnode_sdk.capability_graph import missing_for

    gaps = missing_for({"web_search"}, min_weight=0.3)
    cap_names = [g[0] for g in gaps]
    assert "text_summarization" in cap_names
    assert "webpage_extraction" in cap_names

    for _, score, reason in gaps:
        assert score > 0.0
        assert reason


def test_graph_missing_excludes_installed():
    from agentnode_sdk.capability_graph import missing_for

    gaps = missing_for({"web_search", "text_summarization"}, min_weight=0.3)
    cap_names = [g[0] for g in gaps]
    assert "text_summarization" not in cap_names


def test_graph_priority_ordering():
    from agentnode_sdk.capability_graph import missing_for

    gaps = missing_for({"web_search", "pdf_extraction"}, min_weight=0.3)
    if len(gaps) >= 2:
        scores = [g[1] for g in gaps]
        assert scores == sorted(scores, reverse=True)


def test_graph_multi_source_boosts_priority():
    from agentnode_sdk.capability_graph import missing_for

    single = missing_for({"web_search"}, min_weight=0.3)
    multi = missing_for({"web_search", "pdf_extraction"}, min_weight=0.3)

    single_scores = {g[0]: g[1] for g in single}
    multi_scores = {g[0]: g[1] for g in multi}

    if "text_summarization" in single_scores and "text_summarization" in multi_scores:
        assert multi_scores["text_summarization"] > single_scores["text_summarization"]


def test_graph_requires_direction():
    """requires should only be used where target is truly needed."""
    from agentnode_sdk.capability_graph import CAPABILITY_GRAPH

    vm_edges = CAPABILITY_GRAPH.get("vector_memory", [])
    requires_edges = [e for e in vm_edges if e.relationship == "requires"]
    assert len(requires_edges) == 1
    assert requires_edges[0].target == "embedding_generation"

    eg_edges = CAPABILITY_GRAPH.get("embedding_generation", [])
    eg_requires = [e for e in eg_edges if e.relationship == "requires"]
    assert len(eg_requires) == 0


def test_priority_label():
    from agentnode_sdk.capability_graph import priority_label

    assert priority_label(0.9) == "high"
    assert priority_label(0.5) == "suggested"
    assert priority_label(0.1) == "low"


# --- Synonyms ---


def test_synonym_parse_task():
    from agentnode_sdk.cli.smart_run import parse_task

    result = parse_task("take screenshot of the dashboard")
    assert result is not None
    assert result.capability == "screenshot_capture"
    assert result.source == "synonym"


def test_synonym_regex_takes_priority():
    """Regex patterns should match before synonyms."""
    from agentnode_sdk.cli.smart_run import parse_task

    result = parse_task("search for AI news")
    assert result is not None
    assert result.capability == "web_search"
    assert result.source == "pattern"


def test_synonym_get_alternatives():
    from agentnode_sdk.cli.smart_run import get_alternatives

    alts = get_alternatives("analyze data in my spreadsheet")
    assert "csv_analysis" in alts


def test_synonym_longest_first():
    """Longer synonym phrases should match before shorter ones."""
    from agentnode_sdk.cli.smart_run import parse_task

    result = parse_task("generate sql query for my database")
    assert result is not None
    assert result.capability == "sql_generation"


# --- Re-ranking ---


def test_rerank_boosts_complement():
    from agentnode_sdk.resolve import rerank
    from agentnode_sdk.models import ResolvedPackage, ScoreBreakdown

    breakdown = ScoreBreakdown(50, 10, 10, 10, 10)
    pkg_complement = ResolvedPackage(
        slug="summarizer", name="", package_type="toolpack", summary="",
        version="1.0", publisher_slug="", trust_level="verified",
        score=50.0, breakdown=breakdown,
        matched_capabilities=["text_summarization"],
    )
    pkg_unrelated = ResolvedPackage(
        slug="other", name="", package_type="toolpack", summary="",
        version="1.0", publisher_slug="", trust_level="verified",
        score=51.0, breakdown=breakdown,
        matched_capabilities=["unrelated_cap"],
    )

    ranked = rerank([pkg_unrelated, pkg_complement], {"web_search"}, set())
    assert ranked[0].slug == "summarizer"


def test_rerank_penalizes_overlap():
    from agentnode_sdk.resolve import rerank
    from agentnode_sdk.models import ResolvedPackage, ScoreBreakdown

    breakdown = ScoreBreakdown(50, 10, 10, 10, 10)
    pkg = ResolvedPackage(
        slug="redundant", name="", package_type="toolpack", summary="",
        version="1.0", publisher_slug="", trust_level="verified",
        score=80.0, breakdown=breakdown,
        matched_capabilities=["web_search"],
    )

    ranked = rerank([pkg], {"web_search"}, set())
    assert ranked[0].slug == "redundant"


def test_rerank_penalizes_already_installed():
    from agentnode_sdk.resolve import rerank
    from agentnode_sdk.models import ResolvedPackage, ScoreBreakdown

    breakdown = ScoreBreakdown(50, 10, 10, 10, 10)
    installed_pkg = ResolvedPackage(
        slug="my-pack", name="", package_type="toolpack", summary="",
        version="1.0", publisher_slug="", trust_level="verified",
        score=90.0, breakdown=breakdown,
        matched_capabilities=["web_search"],
    )
    new_pkg = ResolvedPackage(
        slug="new-pack", name="", package_type="toolpack", summary="",
        version="1.0", publisher_slug="", trust_level="verified",
        score=50.0, breakdown=breakdown,
        matched_capabilities=["web_search"],
    )

    ranked = rerank([installed_pkg, new_pkg], set(), {"my-pack"})
    assert ranked[0].slug == "new-pack"


def test_rerank_boost_clamped():
    """Boost should not exceed +10 to prevent server score oversteering."""
    from agentnode_sdk.resolve import rerank
    from agentnode_sdk.models import ResolvedPackage, ScoreBreakdown

    breakdown = ScoreBreakdown(50, 10, 10, 10, 10)
    pkg = ResolvedPackage(
        slug="super-complement", name="", package_type="toolpack", summary="",
        version="1.0", publisher_slug="", trust_level="verified",
        score=40.0, breakdown=breakdown,
        matched_capabilities=["text_summarization", "webpage_extraction", "pdf_extraction"],
    )

    ranked = rerank([pkg], {"web_search", "pdf_extraction", "document_parsing"}, set())
    effective_score = ranked[0].score
    assert effective_score <= 40.0 + 10.0


# --- Backward Compatibility ---


def test_complements_backward_compat():
    from agentnode_sdk.cli.complements import CAPABILITY_COMPLEMENTS

    assert isinstance(CAPABILITY_COMPLEMENTS, dict)
    assert "web_search" in CAPABILITY_COMPLEMENTS
    assert isinstance(CAPABILITY_COMPLEMENTS["web_search"], list)
    assert "text_summarization" in CAPABILITY_COMPLEMENTS["web_search"]
