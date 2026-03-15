"""Tests for webpage-extractor-pack."""


def test_run_returns_structure():
    """Test extraction returns expected structure."""
    from webpage_extractor_pack.tool import run

    # Use a reliable, simple page
    result = run("https://example.com")
    assert "title" in result
    assert "text" in result
    assert "url" in result
    assert result["url"] == "https://example.com"
    # example.com should have some text
    assert len(result["text"]) > 0


def test_run_bad_url():
    """Test graceful handling of unreachable URL."""
    from webpage_extractor_pack.tool import run

    result = run("https://this-url-does-not-exist-12345.invalid")
    assert "error" in result
    assert result["text"] == ""
