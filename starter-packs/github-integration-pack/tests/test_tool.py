"""Tests for github-integration-pack."""

from unittest.mock import MagicMock, patch

from github_integration_pack.tool import run


# -- Input validation --

def test_missing_token():
    result = run(token="", operation="list_repos")
    assert result["success"] is False
    assert "token" in result["error"].lower()


def test_unknown_operation():
    result = run(token="tok", operation="nuke_repo")
    assert result["success"] is False
    assert "Unknown operation" in result["error"]


def test_get_repo_missing_repo():
    result = run(token="tok", operation="get_repo", repo="")
    assert result["success"] is False
    assert "repo" in result["error"].lower()


def test_create_issue_missing_repo():
    result = run(token="tok", operation="create_issue")
    assert result["success"] is False
    assert "repo" in result["error"].lower()


def test_get_file_missing_repo():
    result = run(token="tok", operation="get_file")
    assert result["success"] is False
    assert "repo" in result["error"].lower()


# -- Mocked list_repos --

@patch("github_integration_pack.tool.Github")
@patch("github_integration_pack.tool.Auth.Token")
def test_list_repos(mock_auth, mock_github_cls):
    mock_g = MagicMock()
    mock_github_cls.return_value = mock_g

    mock_repo = MagicMock()
    mock_repo.full_name = "user/repo"
    mock_repo.description = "A test repo"
    mock_repo.language = "Python"
    mock_repo.stargazers_count = 42
    mock_repo.forks_count = 5
    mock_repo.private = False
    mock_repo.html_url = "https://github.com/user/repo"
    mock_repo.updated_at.isoformat.return_value = "2024-01-01"

    mock_g.get_user.return_value.get_repos.return_value.__getitem__ = (
        lambda self, s: [mock_repo]
    )
    mock_g.get_user.return_value.get_repos.return_value = [mock_repo]

    result = run(token="tok", operation="list_repos")
    assert result["success"] is True
    assert result["total"] == 1
    assert result["repos"][0]["full_name"] == "user/repo"


# -- Mocked create_issue --

@patch("github_integration_pack.tool.Github")
@patch("github_integration_pack.tool.Auth.Token")
def test_create_issue(mock_auth, mock_github_cls):
    mock_g = MagicMock()
    mock_github_cls.return_value = mock_g

    mock_issue = MagicMock()
    mock_issue.number = 1
    mock_issue.title = "Bug fix"
    mock_issue.html_url = "https://github.com/user/repo/issues/1"

    mock_repo = MagicMock()
    mock_repo.create_issue.return_value = mock_issue
    mock_g.get_repo.return_value = mock_repo

    result = run(token="tok", operation="create_issue", repo="user/repo",
                 title="Bug fix", body="Something broke")
    assert result["success"] is True
    assert result["issue"]["number"] == 1


# -- create_issue missing title --

@patch("github_integration_pack.tool.Github")
@patch("github_integration_pack.tool.Auth.Token")
def test_create_issue_missing_title(mock_auth, mock_github_cls):
    mock_g = MagicMock()
    mock_github_cls.return_value = mock_g

    result = run(token="tok", operation="create_issue", repo="user/repo")
    assert result["success"] is False
    assert "title" in result["error"].lower()
