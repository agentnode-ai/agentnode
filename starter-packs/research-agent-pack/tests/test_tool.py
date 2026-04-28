"""Tests for research-agent-pack."""

from unittest.mock import MagicMock, patch

from research_agent_pack.tool import run


# -- Mocked successful research --

@patch("research_agent_pack.tool.ResearchAgent")
def test_run_success(mock_agent_cls):
    mock_agent = MagicMock()
    mock_agent_cls.return_value = mock_agent
    mock_agent.research.return_value = {
        "query": "quantum computing",
        "sources": [
            {"title": "Quantum 101", "url": "https://example.com/q", "snippet": "Intro",
             "source_type": "web"},
        ],
        "findings": [{"source": "Quantum 101", "content": "Quantum computers use qubits."}],
        "summary": "Quantum computing uses qubits for computation.",
        "metadata": {"timestamp": "2024-01-01T00:00:00Z", "source_count": 1},
    }

    result = run(query="quantum computing", max_sources=5)
    assert result["query"] == "quantum computing"
    assert len(result["sources"]) == 1
    assert len(result["findings"]) == 1
    assert "qubits" in result["summary"]
    mock_agent.research.assert_called_once_with(
        query="quantum computing", pdf_path=None, max_sources=5,
    )


# -- max_sources clamping --

@patch("research_agent_pack.tool.ResearchAgent")
def test_max_sources_clamped_low(mock_agent_cls):
    mock_agent = MagicMock()
    mock_agent_cls.return_value = mock_agent
    mock_agent.research.return_value = {"query": "q", "sources": [], "findings": [],
                                         "summary": "", "metadata": {}}

    run(query="test", max_sources=-5)
    call_kwargs = mock_agent.research.call_args[1]
    assert call_kwargs["max_sources"] == 1


@patch("research_agent_pack.tool.ResearchAgent")
def test_max_sources_clamped_high(mock_agent_cls):
    mock_agent = MagicMock()
    mock_agent_cls.return_value = mock_agent
    mock_agent.research.return_value = {"query": "q", "sources": [], "findings": [],
                                         "summary": "", "metadata": {}}

    run(query="test", max_sources=100)
    call_kwargs = mock_agent.research.call_args[1]
    assert call_kwargs["max_sources"] == 20


# -- PDF path handling --

@patch("research_agent_pack.tool.ResearchAgent")
def test_empty_pdf_path(mock_agent_cls):
    mock_agent = MagicMock()
    mock_agent_cls.return_value = mock_agent
    mock_agent.research.return_value = {"query": "q", "sources": [], "findings": [],
                                         "summary": "", "metadata": {}}

    run(query="test", pdf_path="")
    call_kwargs = mock_agent.research.call_args[1]
    assert call_kwargs["pdf_path"] is None


@patch("os.path.isfile", return_value=False)
@patch("research_agent_pack.tool.ResearchAgent")
def test_nonexistent_pdf_path(mock_agent_cls, mock_isfile):
    mock_agent = MagicMock()
    mock_agent_cls.return_value = mock_agent
    mock_agent.research.return_value = {"query": "q", "sources": [], "findings": [],
                                         "summary": "", "metadata": {}}

    run(query="test", pdf_path="/nonexistent/file.pdf")
    call_kwargs = mock_agent.research.call_args[1]
    assert call_kwargs["pdf_path"] is None


@patch("os.path.isfile", return_value=True)
@patch("research_agent_pack.tool.ResearchAgent")
def test_valid_pdf_path(mock_agent_cls, mock_isfile):
    mock_agent = MagicMock()
    mock_agent_cls.return_value = mock_agent
    mock_agent.research.return_value = {"query": "q", "sources": [], "findings": [],
                                         "summary": "", "metadata": {}}

    run(query="test", pdf_path="/tmp/paper.pdf")
    call_kwargs = mock_agent.research.call_args[1]
    assert call_kwargs["pdf_path"] == "/tmp/paper.pdf"
