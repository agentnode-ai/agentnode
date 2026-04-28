"""Tests for home-automation-pack."""

import pytest
from unittest.mock import MagicMock, patch

from home_automation_pack.tool import run


# -- Input validation --

def test_unknown_operation():
    with pytest.raises(ValueError, match="Unknown operation"):
        run(ha_url="http://ha:8123", token="tok", operation="self_destruct")


def test_get_state_missing_entity():
    with pytest.raises(ValueError, match="entity_id is required"):
        run(ha_url="http://ha:8123", token="tok", operation="get_state", entity_id="")


def test_turn_on_missing_entity():
    with pytest.raises(ValueError, match="entity_id is required"):
        run(ha_url="http://ha:8123", token="tok", operation="turn_on", entity_id="")


def test_call_service_missing_domain():
    with pytest.raises(ValueError, match="domain and service are required"):
        run(ha_url="http://ha:8123", token="tok", operation="call_service")


# -- Mocked list_entities --

@patch("home_automation_pack.tool.httpx.Client")
def test_list_entities(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = [
        {"entity_id": "light.living_room", "state": "on",
         "attributes": {"friendly_name": "Living Room Light"}},
    ]
    mock_client.get.return_value = mock_resp

    result = run(ha_url="http://ha:8123", token="tok", operation="list_entities")
    assert result["total"] == 1
    assert result["entities"][0]["entity_id"] == "light.living_room"


# -- Mocked turn_on --

@patch("home_automation_pack.tool.httpx.Client")
def test_turn_on(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_client.post.return_value = mock_resp

    result = run(ha_url="http://ha:8123", token="tok", operation="turn_on",
                 entity_id="light.living_room")
    assert result["status"] == "success"
    assert result["entity_id"] == "light.living_room"


# -- Mocked get_state --

@patch("home_automation_pack.tool.httpx.Client")
def test_get_state(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "entity_id": "sensor.temp", "state": "22.5",
        "attributes": {"unit": "C"}, "last_changed": "2024-01-01",
        "last_updated": "2024-01-01",
    }
    mock_client.get.return_value = mock_resp

    result = run(ha_url="http://ha:8123", token="tok", operation="get_state",
                 entity_id="sensor.temp")
    assert result["state"] == "22.5"
