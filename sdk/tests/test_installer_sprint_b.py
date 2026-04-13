"""Sprint B tests for the SDK installer (P1-SDK6: download size ceiling)."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from agentnode_sdk.installer import MAX_DOWNLOAD_BYTES, download_artifact


@respx.mock
def test_download_rejects_oversized_content_length(tmp_path: Path):
    """P1-SDK6: a server declaring a huge Content-Length must be refused
    before any bytes are written."""
    url = "https://s3.example.com/huge.tar.gz"
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            content=b"x" * 1024,
            headers={"Content-Length": str(MAX_DOWNLOAD_BYTES * 2)},
        )
    )
    dest = tmp_path / "out.tar.gz"
    with pytest.raises(RuntimeError, match="too large"):
        download_artifact(url, dest, max_bytes=MAX_DOWNLOAD_BYTES)


@respx.mock
def test_download_enforces_ceiling(tmp_path: Path):
    """P1-SDK6: oversized downloads must be refused with RuntimeError."""
    url = "https://s3.example.com/unbounded.tar.gz"
    payload = b"y" * (10 * 1024)  # 10 KiB, exceeds max_bytes=4096 below
    respx.get(url).mock(return_value=httpx.Response(200, content=payload))
    dest = tmp_path / "out.tar.gz"
    with pytest.raises(RuntimeError):
        download_artifact(url, dest, max_bytes=4096)
    # Aborted downloads must not leave a file behind.
    assert not dest.exists()


@respx.mock
def test_download_succeeds_under_limit(tmp_path: Path):
    """Happy path: a normal-sized download should still work."""
    url = "https://s3.example.com/ok.tar.gz"
    payload = b"hello-world"
    respx.get(url).mock(return_value=httpx.Response(200, content=payload))
    dest = tmp_path / "out.tar.gz"
    download_artifact(url, dest, max_bytes=1024 * 1024)
    assert dest.read_bytes() == payload
