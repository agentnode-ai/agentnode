"""Tests for project-board-pack."""

import pytest
from unittest.mock import MagicMock, patch

from project_board_pack.tool import run


# -- Input validation --

def test_missing_token():
    with pytest.raises(ValueError, match="token is required"):
        run(api_key="key", operation="list_boards")


def test_unknown_operation():
    with pytest.raises(ValueError, match="Unknown operation"):
        run(api_key="key", operation="nuke_board", token="tok")


def test_get_board_missing_id():
    with pytest.raises(ValueError, match="board_id is required"):
        run(api_key="key", operation="get_board", token="tok")


def test_list_cards_missing_list_id():
    with pytest.raises(ValueError, match="list_id is required"):
        run(api_key="key", operation="list_cards", token="tok")


def test_create_card_missing_name():
    with pytest.raises(ValueError, match="name is required"):
        run(api_key="key", operation="create_card", token="tok", list_id="lst1")


def test_move_card_missing_card_id():
    with pytest.raises(ValueError, match="card_id is required"):
        run(api_key="key", operation="move_card", token="tok", list_id="lst1")


# -- Mocked list_boards --

@patch("httpx.Client")
def test_list_boards(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = [{
        "id": "b1", "name": "Sprint Board", "desc": "Current sprint",
        "url": "https://trello.com/b/b1", "closed": False,
        "dateLastActivity": "2024-01-01",
    }]
    mock_client.get.return_value = mock_resp

    result = run(api_key="key", operation="list_boards", token="tok")
    assert result["total"] == 1
    assert result["boards"][0]["name"] == "Sprint Board"


# -- Mocked create_card --

@patch("httpx.Client")
def test_create_card(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "id": "c1", "name": "New Task", "url": "https://trello.com/c/c1",
    }
    mock_client.post.return_value = mock_resp

    result = run(api_key="key", operation="create_card", token="tok",
                 list_id="lst1", name="New Task")
    assert result["status"] == "created"
    assert result["id"] == "c1"
