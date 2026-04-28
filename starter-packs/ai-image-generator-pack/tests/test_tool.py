"""Tests for ai-image-generator-pack."""

import base64
import os
import pytest
from unittest.mock import MagicMock, patch

from ai_image_generator_pack.tool import run


# -- Input validation --

def test_missing_api_key():
    with pytest.raises(ValueError, match="api_key is required"):
        run(prompt="a cat", api_key="")


def test_unsupported_provider():
    with pytest.raises(ValueError, match="Unsupported provider"):
        run(prompt="a cat", api_key="key", provider="midjourney")


# -- Mocked Stability AI --

@patch("ai_image_generator_pack.tool.httpx.Client")
def test_stability_success(mock_cls, tmp_path):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    fake_image = b"FAKEPNGDATA"
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "artifacts": [{"base64": base64.b64encode(fake_image).decode()}],
    }
    mock_client.post.return_value = mock_resp

    out_file = str(tmp_path / "out.png")
    result = run(prompt="a cat", api_key="key", provider="stability", output_path=out_file)
    assert result["provider"] == "stability"
    assert result["prompt"] == "a cat"
    assert os.path.isfile(out_file)

    with open(out_file, "rb") as f:
        assert f.read() == fake_image


# -- Mocked Stability AI no artifacts --

@patch("ai_image_generator_pack.tool.httpx.Client")
def test_stability_no_artifacts(mock_cls, tmp_path):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"artifacts": []}
    mock_client.post.return_value = mock_resp

    with pytest.raises(RuntimeError, match="No image returned"):
        run(prompt="a cat", api_key="key", provider="stability",
            output_path=str(tmp_path / "out.png"))


# -- Mocked Replicate --

@patch("ai_image_generator_pack.tool.httpx.Client")
def test_replicate_success(mock_cls, tmp_path):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    fake_image = b"REPLICATE_IMG"

    # First call: create prediction (already succeeded)
    predict_resp = MagicMock()
    predict_resp.raise_for_status = MagicMock()
    predict_resp.json.return_value = {
        "status": "succeeded",
        "output": ["https://replicate.example.com/img.png"],
        "urls": {"get": "https://api.replicate.com/v1/predictions/p1"},
    }

    # Second call: fetch image bytes
    img_resp = MagicMock()
    img_resp.raise_for_status = MagicMock()
    img_resp.content = fake_image

    mock_client.post.return_value = predict_resp
    mock_client.get.return_value = img_resp

    out_file = str(tmp_path / "rep.png")
    result = run(prompt="a dog", api_key="key", provider="replicate", output_path=out_file)
    assert result["provider"] == "replicate"
    assert os.path.isfile(out_file)
