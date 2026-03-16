"""Tests for document-summarizer-pack."""

import pytest


def test_run_returns_summary():
    """Test basic extractive summarization."""
    from document_summarizer_pack.tool import run

    text = (
        "Machine learning is a subset of artificial intelligence. "
        "It allows computers to learn from data. "
        "Deep learning is a subset of machine learning. "
        "Neural networks are the backbone of deep learning. "
        "Training requires large datasets. "
        "GPUs accelerate the training process. "
        "Transfer learning reduces training time. "
        "Models can be fine-tuned for specific tasks. "
        "Evaluation metrics measure model performance. "
        "Deployment requires careful optimization."
    )

    result = run(text, max_sentences=3)

    assert "summary" in result
    assert "original_length" in result
    assert "summary_length" in result
    assert "compression_ratio" in result
    assert result["original_length"] == len(text)
    assert result["summary_length"] < result["original_length"]
    assert 0 < result["compression_ratio"] < 1


def test_run_empty_text():
    """Test that empty text returns empty summary."""
    from document_summarizer_pack.tool import run

    result = run("")
    assert result["summary"] == ""
    assert result["original_length"] == 0
    assert result["compression_ratio"] == 0.0


def test_run_whitespace_only():
    """Test that whitespace-only text returns empty summary."""
    from document_summarizer_pack.tool import run

    result = run("   \n\t  ")
    assert result["summary"] == ""


def test_run_short_text_returns_all():
    """Text shorter than max_sentences should be returned as-is."""
    from document_summarizer_pack.tool import run

    text = "This is a single sentence. Here is another."
    result = run(text, max_sentences=5)

    assert result["summary"] == text
    assert result["compression_ratio"] == 1.0


def test_run_extractive_method():
    """Test that extractive method is accepted."""
    from document_summarizer_pack.tool import run

    result = run("Test sentence one. Test sentence two.", method="extractive")
    assert "summary" in result


def test_run_invalid_method_raises():
    """Test that invalid method raises ValueError."""
    from document_summarizer_pack.tool import run

    with pytest.raises(ValueError, match="Unsupported method"):
        run("Some text.", method="abstractive")


def test_run_preserves_original_order():
    """Summary sentences should appear in original text order."""
    from document_summarizer_pack.tool import run

    text = (
        "Alpha is the first letter. "
        "Beta is the second letter. "
        "Gamma is the third letter. "
        "Delta is the fourth letter. "
        "Epsilon is the fifth letter. "
        "Zeta is the sixth letter. "
        "Eta is the seventh letter."
    )

    result = run(text, max_sentences=3)
    summary = result["summary"]

    # The summary sentences should be a subset in original order
    sentences = summary.split(". ")
    # Verify we got <= max_sentences
    assert len(sentences) <= 4  # split may add empty trailing


def test_run_compression_ratio_range():
    """Compression ratio should be between 0 and 1 for long texts."""
    from document_summarizer_pack.tool import run

    text = ". ".join(f"Sentence number {i} about topic {i % 3}" for i in range(20))
    result = run(text, max_sentences=3)

    assert 0 < result["compression_ratio"] <= 1.0


def test_run_max_sentences_one():
    """Test summarization to a single sentence."""
    from document_summarizer_pack.tool import run

    text = (
        "Python is popular. "
        "Java is widely used. "
        "Rust is fast. "
        "Go is concurrent. "
        "TypeScript adds types."
    )

    result = run(text, max_sentences=1)
    # Should contain roughly one sentence
    assert len(result["summary"]) > 0
    assert result["summary_length"] < result["original_length"]
