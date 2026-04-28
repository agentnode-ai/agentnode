"""Tests for ci-cd-runner-pack."""

from unittest.mock import MagicMock, patch

from ci_cd_runner_pack.tool import run


# -- Input validation --

def test_unknown_platform():
    result = run(platform="jenkins", token="tok", operation="list_runs")
    assert result["status"] == "error"
    assert "Unknown platform" in result["message"]


def test_github_missing_repo():
    result = run(platform="github", token="tok", operation="list_runs")
    assert result["status"] == "error"
    assert "repo" in result["message"].lower()


def test_github_unknown_operation():
    result = run(platform="github", token="tok", operation="nuke", repo="o/r")
    assert result["status"] == "error"
    assert "Unknown GitHub operation" in result["message"]


def test_gitlab_missing_project_id():
    result = run(platform="gitlab", token="tok", operation="list_pipelines")
    assert result["status"] == "error"
    assert "project_id" in result["message"]


def test_gitlab_unknown_operation():
    result = run(platform="gitlab", token="tok", operation="explode", project_id="42")
    assert result["status"] == "error"


# -- Mocked GitHub success --

@patch("ci_cd_runner_pack.tool.httpx.get")
def test_github_list_runs(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "total_count": 1,
        "workflow_runs": [{
            "id": 99, "name": "CI", "status": "completed", "conclusion": "success",
            "head_branch": "main", "event": "push", "created_at": "2024-01-01",
            "html_url": "https://github.com/o/r/actions/runs/99",
        }],
    }
    mock_get.return_value = mock_resp

    result = run(platform="github", token="tok", operation="list_runs", repo="o/r")
    assert result["status"] == "ok"
    assert result["total_count"] == 1
    assert result["runs"][0]["id"] == 99


# -- Mocked GitLab success --

@patch("ci_cd_runner_pack.tool.httpx.get")
def test_gitlab_list_pipelines(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = [{
        "id": 10, "status": "success", "ref": "main", "sha": "abcdef123456",
        "created_at": "2024-01-01", "updated_at": "2024-01-01",
        "web_url": "https://gitlab.com/p/10",
    }]
    mock_get.return_value = mock_resp

    result = run(platform="gitlab", token="tok", operation="list_pipelines", project_id="42")
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["pipelines"][0]["id"] == 10


# -- Error propagation --

@patch("ci_cd_runner_pack.tool.httpx.get")
def test_github_http_error(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.text = "Forbidden"
    import httpx
    mock_get.side_effect = httpx.HTTPStatusError("err", request=MagicMock(), response=mock_resp)

    result = run(platform="github", token="tok", operation="list_runs", repo="o/r")
    assert result["status"] == "error"
    assert "403" in result["message"]
