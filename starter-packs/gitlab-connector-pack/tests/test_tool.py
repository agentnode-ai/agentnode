"""Tests for gitlab-connector-pack."""

from unittest.mock import MagicMock, patch

from gitlab_connector_pack.tool import run


# -- Input validation --

def test_missing_url():
    result = run(url="", token="tok", operation="list_projects")
    assert result["success"] is False
    assert "url" in result["error"].lower()


def test_missing_token():
    result = run(url="https://gitlab.com", token="", operation="list_projects")
    assert result["success"] is False
    assert "token" in result["error"].lower()


def test_unknown_operation():
    result = run(url="https://gitlab.com", token="tok", operation="delete_all")
    assert result["success"] is False
    assert "Unknown operation" in result["error"]


def test_get_project_missing_project():
    result = run(url="https://gitlab.com", token="tok", operation="get_project")
    assert result["success"] is False
    assert "project" in result["error"].lower()


def test_get_file_missing_project():
    result = run(url="https://gitlab.com", token="tok", operation="get_file")
    assert result["success"] is False
    assert "project" in result["error"].lower()


# -- Mocked list_projects --

@patch("gitlab_connector_pack.tool.gitlab.Gitlab")
def test_list_projects(mock_gl_cls):
    mock_gl = MagicMock()
    mock_gl_cls.return_value = mock_gl

    mock_project = MagicMock()
    mock_project.id = 1
    mock_project.name = "my-project"
    mock_project.path_with_namespace = "user/my-project"
    mock_project.description = "A project"
    mock_project.visibility = "public"
    mock_project.default_branch = "main"
    mock_project.web_url = "https://gitlab.com/user/my-project"
    mock_project.star_count = 5
    mock_project.forks_count = 2
    mock_project.last_activity_at = "2024-01-01"

    mock_gl.projects.list.return_value = [mock_project]

    result = run(url="https://gitlab.com", token="tok", operation="list_projects")
    assert result["success"] is True
    assert result["total"] == 1
    assert result["projects"][0]["name"] == "my-project"


# -- Mocked list_mrs --

@patch("gitlab_connector_pack.tool.gitlab.Gitlab")
def test_list_mrs(mock_gl_cls):
    mock_gl = MagicMock()
    mock_gl_cls.return_value = mock_gl

    mock_mr = MagicMock()
    mock_mr.iid = 10
    mock_mr.title = "Feature branch"
    mock_mr.state = "opened"
    mock_mr.author = {"username": "dev"}
    mock_mr.source_branch = "feature"
    mock_mr.target_branch = "main"
    mock_mr.merge_status = "can_be_merged"
    mock_mr.created_at = "2024-01-01"
    mock_mr.updated_at = "2024-01-02"
    mock_mr.web_url = "https://gitlab.com/user/project/-/merge_requests/10"

    mock_project = MagicMock()
    mock_project.mergerequests.list.return_value = [mock_mr]
    mock_gl.projects.get.return_value = mock_project

    result = run(url="https://gitlab.com", token="tok", operation="list_mrs",
                 project="user/project")
    assert result["success"] is True
    assert result["total"] == 1
    assert result["merge_requests"][0]["title"] == "Feature branch"


# -- Auth error --

@patch("gitlab_connector_pack.tool.gitlab.Gitlab")
def test_auth_error(mock_gl_cls):
    import gitlab as gl_lib
    mock_gl = MagicMock()
    mock_gl.auth.side_effect = gl_lib.exceptions.GitlabAuthenticationError("Invalid token")
    mock_gl_cls.return_value = mock_gl

    result = run(url="https://gitlab.com", token="bad", operation="list_projects")
    assert result["success"] is False
    assert "Authentication" in result["error"]
