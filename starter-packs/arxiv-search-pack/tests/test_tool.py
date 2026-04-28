"""Tests for arxiv-search-pack."""

import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

from arxiv_search_pack.tool import _parse_entry, run, _ATOM, _OPENSEARCH


# -- Pure helper: _parse_entry --

def _make_entry_xml(title="Test Paper", author="Alice", url="http://arxiv.org/abs/1234"):
    xml_str = f"""<entry xmlns="{_ATOM}">
        <title>{title}</title>
        <summary>An abstract about transformers.</summary>
        <author><name>{author}</name></author>
        <link rel="alternate" href="{url}"/>
        <published>2024-01-01T00:00:00Z</published>
        <category term="cs.AI"/>
    </entry>"""
    return ET.fromstring(xml_str)


def test_parse_entry_extracts_fields():
    entry = _make_entry_xml()
    result = _parse_entry(entry)
    assert result["title"] == "Test Paper"
    assert result["authors"] == ["Alice"]
    assert result["url"] == "http://arxiv.org/abs/1234"
    assert "cs.AI" in result["categories"]
    assert "abstract" in result["abstract"].lower() or len(result["abstract"]) > 0


def test_parse_entry_empty_title():
    xml_str = f'<entry xmlns="{_ATOM}"><title></title></entry>'
    entry = ET.fromstring(xml_str)
    result = _parse_entry(entry)
    assert result["title"] == ""


# -- Mocked run() --

MOCK_ARXIV_RESPONSE = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="{_ATOM}" xmlns:opensearch="{_OPENSEARCH}">
  <opensearch:totalResults>1</opensearch:totalResults>
  <entry>
    <title>Attention Is All You Need</title>
    <summary>We propose a new architecture.</summary>
    <author><name>Vaswani</name></author>
    <link rel="alternate" href="http://arxiv.org/abs/1706.03762"/>
    <published>2017-06-12T00:00:00Z</published>
    <category term="cs.CL"/>
  </entry>
</feed>"""


@patch("arxiv_search_pack.tool.httpx.Client")
def test_run_success(mock_client_cls):
    mock_resp = MagicMock()
    mock_resp.text = MOCK_ARXIV_RESPONSE
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get.return_value = mock_resp
    mock_client_cls.return_value = mock_client

    result = run(query="transformer attention", max_results=5)
    assert result["total"] == 1
    assert len(result["papers"]) == 1
    assert result["papers"][0]["title"] == "Attention Is All You Need"

    mock_client.close.assert_called_once()


# -- Sort normalization --

def test_sort_by_aliases():
    from arxiv_search_pack.tool import _SORT_MAP
    assert _SORT_MAP["date"] == "submittedDate"
    assert _SORT_MAP["relevance"] == "relevance"
    assert _SORT_MAP["last_updated"] == "lastUpdatedDate"
