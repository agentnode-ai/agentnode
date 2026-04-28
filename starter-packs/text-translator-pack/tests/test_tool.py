"""Tests for text-translator-pack."""

from unittest.mock import MagicMock, patch

from text_translator_pack.tool import run


# -- Empty text --

def test_empty_text():
    result = run(text="", target_language="fr")
    assert result["translated_text"] == ""
    assert result["target_language"] == "fr"
    assert result["source_language"] == "auto"


def test_whitespace_only():
    result = run(text="   ", target_language="de")
    assert result["translated_text"] == ""


# -- Mocked translation --

@patch("text_translator_pack.tool.GoogleTranslator")
def test_translate_en_to_fr(mock_translator_cls):
    mock_translator = MagicMock()
    mock_translator.translate.return_value = "Bonjour le monde"
    mock_translator_cls.return_value = mock_translator

    result = run(text="Hello world", target_language="fr")
    assert result["translated_text"] == "Bonjour le monde"
    assert result["target_language"] == "fr"
    assert result["source_language"] == "auto"
    mock_translator_cls.assert_called_once_with(source="auto", target="fr")


@patch("text_translator_pack.tool.GoogleTranslator")
def test_translate_with_source_language(mock_translator_cls):
    mock_translator = MagicMock()
    mock_translator.translate.return_value = "Hola mundo"
    mock_translator_cls.return_value = mock_translator

    result = run(text="Hello world", target_language="es", source_language="en")
    assert result["translated_text"] == "Hola mundo"
    assert result["source_language"] == "en"
    mock_translator_cls.assert_called_once_with(source="en", target="es")


@patch("text_translator_pack.tool.GoogleTranslator")
def test_translate_preserves_original_params(mock_translator_cls):
    mock_translator = MagicMock()
    mock_translator.translate.return_value = "Test"
    mock_translator_cls.return_value = mock_translator

    result = run(text="Test", target_language="ja", source_language="en")
    assert result["target_language"] == "ja"
    assert result["source_language"] == "en"


# -- Return structure --

@patch("text_translator_pack.tool.GoogleTranslator")
def test_return_keys(mock_translator_cls):
    mock_translator = MagicMock()
    mock_translator.translate.return_value = "X"
    mock_translator_cls.return_value = mock_translator

    result = run(text="Y", target_language="en")
    assert set(result.keys()) == {"translated_text", "source_language", "target_language"}
