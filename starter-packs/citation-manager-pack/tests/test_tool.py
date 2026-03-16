"""Tests for citation-manager-pack."""


def test_run_apa_book():
    from citation_manager_pack.tool import run

    result = run(
        source={"type": "book", "authors": ["John Smith"], "title": "Test Book", "year": "2024"},
        style="apa",
    )
    assert "citation" in result
    assert "bibtex" in result
    assert result["style"] == "apa"
    assert "Smith" in result["citation"]
    assert "2024" in result["citation"]


def test_run_mla_style():
    from citation_manager_pack.tool import run

    result = run(
        source={"type": "article", "authors": ["Jane Doe"], "title": "Test Article", "year": "2023"},
        style="mla",
    )
    assert result["style"] == "mla"
    assert len(result["citation"]) > 0
