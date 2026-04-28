"""Tests for task-manager-pack."""

import pytest
from unittest.mock import MagicMock, patch

from task_manager_pack.tool import run


# -- Input validation --

def test_unsupported_provider():
    with pytest.raises(ValueError, match="Unsupported provider"):
        run(provider="jira", api_key="key", operation="list_issues")


def test_unknown_operation():
    with pytest.raises(ValueError, match="Unknown operation"):
        run(provider="linear", api_key="key", operation="destroy")


def test_create_issue_missing_title():
    with pytest.raises(ValueError, match="title is required"):
        run(provider="linear", api_key="key", operation="create_issue", team_id="t1")


def test_create_issue_missing_team_id():
    with pytest.raises(ValueError, match="team_id is required"):
        run(provider="linear", api_key="key", operation="create_issue", title="Bug")


def test_update_issue_missing_id():
    with pytest.raises(ValueError, match="issue_id is required"):
        run(provider="linear", api_key="key", operation="update_issue", state="s1")


def test_update_issue_no_fields():
    with pytest.raises(ValueError, match="At least one field"):
        run(provider="linear", api_key="key", operation="update_issue", issue_id="i1")


# -- Mocked list_issues --

@patch("httpx.Client")
def test_list_issues(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "data": {
            "issues": {"nodes": [{
                "id": "i1", "identifier": "ENG-1", "title": "Fix bug",
                "description": "It is broken", "priority": 1,
                "state": {"id": "s1", "name": "Todo"},
                "assignee": {"name": "Alice", "email": "a@b.com"},
                "createdAt": "2024-01-01", "updatedAt": "2024-01-02",
            }]},
        },
    }
    mock_client.post.return_value = mock_resp

    result = run(provider="linear", api_key="key", operation="list_issues")
    assert result["total"] == 1
    assert result["issues"][0]["identifier"] == "ENG-1"
    assert result["issues"][0]["state"] == "Todo"


# -- Mocked create_issue --

@patch("httpx.Client")
def test_create_issue(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "data": {
            "issueCreate": {
                "success": True,
                "issue": {"id": "i2", "identifier": "ENG-2", "title": "New",
                          "url": "https://linear.app/i2", "state": {"name": "Backlog"}},
            },
        },
    }
    mock_client.post.return_value = mock_resp

    result = run(provider="linear", api_key="key", operation="create_issue",
                 title="New", team_id="t1")
    assert result["status"] == "created"
    assert result["identifier"] == "ENG-2"
