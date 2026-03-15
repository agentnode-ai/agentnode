"""Unit tests for typosquatting detection."""
from app.packages.typosquatting import check_typosquatting


def test_no_similar_slugs():
    result = check_typosquatting("pdf-reader-pack", ["web-search-pack", "document-summary-pack"])
    assert result == []


def test_high_similarity():
    result = check_typosquatting("pdf-reeader-pack", ["pdf-reader-pack", "web-search-pack"])
    assert "pdf-reader-pack" in result


def test_normalized_match():
    result = check_typosquatting("pdf_reader_pack", ["pdf-reader-pack"])
    assert "pdf-reader-pack" in result


def test_exact_same_slug_ignored():
    result = check_typosquatting("pdf-reader-pack", ["pdf-reader-pack"])
    assert result == []


def test_completely_different():
    result = check_typosquatting("my-cool-tool", ["another-thing", "something-else"])
    assert result == []


def test_single_char_difference():
    result = check_typosquatting("pdf-raeder-pack", ["pdf-reader-pack"])
    assert "pdf-reader-pack" in result


def test_extra_hyphen():
    result = check_typosquatting("pdf--reader-pack", ["pdf-reader-pack"])
    assert "pdf-reader-pack" in result


def test_empty_existing_slugs():
    result = check_typosquatting("my-pack", [])
    assert result == []


def test_short_slugs_not_flagged():
    """Short, very different slugs should not be flagged."""
    result = check_typosquatting("abc", ["xyz", "def"])
    assert result == []


def test_multiple_similar_slugs():
    result = check_typosquatting(
        "pdf-reader-pak",
        ["pdf-reader-pack", "pdf-readers-pack", "web-search-pack"],
    )
    assert "pdf-reader-pack" in result


def test_hyphen_underscore_normalization():
    """Hyphens and underscores should be treated as equivalent."""
    result = check_typosquatting("web-search-pack", ["web_search_pack"])
    assert "web_search_pack" in result


def test_no_false_positive_different_domains():
    """Packs in completely different domains shouldn't match."""
    result = check_typosquatting("email-sender-tool", ["pdf-reader-pack", "web-search-pack"])
    assert result == []
