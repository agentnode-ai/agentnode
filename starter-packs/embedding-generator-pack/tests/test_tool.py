"""Tests for embedding-generator-pack."""

import numpy as np
from unittest.mock import MagicMock, patch

from embedding_generator_pack.tool import run


# -- Mocked single text --

@patch("sentence_transformers.SentenceTransformer")
def test_single_text(mock_st_cls):
    mock_model = MagicMock()
    mock_st_cls.return_value = mock_model

    fake_embedding = np.array([[0.1, 0.2, 0.3, 0.4]])
    mock_model.encode.return_value = fake_embedding

    result = run(texts="hello world")
    assert result["num_texts"] == 1
    assert result["dimensions"] == 4
    assert result["model"] == "all-MiniLM-L6-v2"
    assert len(result["embeddings"]) == 1
    assert result["embeddings"][0] == [0.1, 0.2, 0.3, 0.4]


# -- Mocked multiple texts --

@patch("sentence_transformers.SentenceTransformer")
def test_multiple_texts(mock_st_cls):
    mock_model = MagicMock()
    mock_st_cls.return_value = mock_model

    fake_embeddings = np.array([
        [0.1, 0.2, 0.3],
        [0.4, 0.5, 0.6],
        [0.7, 0.8, 0.9],
    ])
    mock_model.encode.return_value = fake_embeddings

    result = run(texts=["hello", "world", "test"])
    assert result["num_texts"] == 3
    assert result["dimensions"] == 3
    assert len(result["embeddings"]) == 3


# -- String input converts to list --

@patch("sentence_transformers.SentenceTransformer")
def test_string_becomes_list(mock_st_cls):
    mock_model = MagicMock()
    mock_st_cls.return_value = mock_model
    mock_model.encode.return_value = np.array([[1.0, 2.0]])

    result = run(texts="single string")
    mock_model.encode.assert_called_once_with(["single string"], convert_to_numpy=True)
    assert result["num_texts"] == 1


# -- Custom model --

@patch("sentence_transformers.SentenceTransformer")
def test_custom_model(mock_st_cls):
    mock_model = MagicMock()
    mock_st_cls.return_value = mock_model
    mock_model.encode.return_value = np.array([[1.0]])

    result = run(texts=["test"], model_name="paraphrase-MiniLM-L6-v2")
    assert result["model"] == "paraphrase-MiniLM-L6-v2"
    mock_st_cls.assert_called_once_with("paraphrase-MiniLM-L6-v2")


# -- Return structure --

@patch("sentence_transformers.SentenceTransformer")
def test_return_keys(mock_st_cls):
    mock_model = MagicMock()
    mock_st_cls.return_value = mock_model
    mock_model.encode.return_value = np.array([[1.0, 2.0]])

    result = run(texts="x")
    assert set(result.keys()) == {"embeddings", "model", "dimensions", "num_texts"}
