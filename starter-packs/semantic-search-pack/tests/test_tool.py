"""Tests for semantic-search-pack."""

import numpy as np
from unittest.mock import MagicMock, patch


# -- Empty documents --

@patch("sentence_transformers.SentenceTransformer")
def test_empty_documents(mock_st_cls):
    from semantic_search_pack.tool import run

    result = run(query="test", documents=[])
    assert result["results"] == []
    assert result["model"] == "all-MiniLM-L6-v2"
    mock_st_cls.assert_not_called()


# -- Mocked search --

@patch("sentence_transformers.SentenceTransformer")
def test_search_returns_ranked_results(mock_st_cls):
    from semantic_search_pack.tool import run

    mock_model = MagicMock()
    mock_st_cls.return_value = mock_model

    query_emb = np.array([1.0, 0.0, 0.0])
    doc_embs = np.array([
        [0.9, 0.1, 0.0],
        [0.0, 1.0, 0.0],
        [0.7, 0.3, 0.0],
    ])

    mock_model.encode.side_effect = [
        np.array([query_emb]),
        doc_embs,
    ]

    docs = ["AI paper", "Sports news", "ML tutorial"]
    result = run(query="machine learning", documents=docs, top_k=2)
    assert len(result["results"]) == 2
    assert result["model"] == "all-MiniLM-L6-v2"
    assert result["results"][0]["index"] == 0


# -- top_k clamping --

@patch("sentence_transformers.SentenceTransformer")
def test_top_k_clamped_to_doc_count(mock_st_cls):
    from semantic_search_pack.tool import run

    mock_model = MagicMock()
    mock_st_cls.return_value = mock_model

    mock_model.encode.side_effect = [
        np.array([[1.0]]),
        np.array([[0.8], [0.5]]),
    ]

    result = run(query="test", documents=["a", "b"], top_k=100)
    assert len(result["results"]) == 2


# -- Custom model name --

@patch("sentence_transformers.SentenceTransformer")
def test_custom_model(mock_st_cls):
    from semantic_search_pack.tool import run

    mock_model = MagicMock()
    mock_st_cls.return_value = mock_model

    mock_model.encode.side_effect = [
        np.array([[1.0]]),
        np.array([[0.9]]),
    ]

    result = run(query="q", documents=["d"], model_name="custom-model")
    assert result["model"] == "custom-model"
    mock_st_cls.assert_called_once_with("custom-model")
