"""Tests for smart-lights-pack."""

import pytest
from unittest.mock import MagicMock, patch

from smart_lights_pack.tool import _hex_to_xy, run


# -- Pure helper --

def test_hex_to_xy_red():
    xy = _hex_to_xy("#ff0000")
    assert len(xy) == 2
    assert xy[0] > 0.5  # Red has high x in CIE
    assert isinstance(xy[0], float)


def test_hex_to_xy_black():
    xy = _hex_to_xy("#000000")
    assert xy == [0.0, 0.0]


def test_hex_to_xy_no_hash():
    xy = _hex_to_xy("00ff00")
    assert len(xy) == 2
    assert xy[1] > xy[0]  # Green has higher y than x


# -- Input validation --

def test_unknown_operation():
    with pytest.raises(ValueError, match="Unknown operation"):
        run(bridge_ip="192.168.1.10", api_key="abc", operation="explode")


def test_get_state_missing_light_id():
    with pytest.raises(ValueError, match="light_id is required"):
        run(bridge_ip="192.168.1.10", api_key="abc", operation="get_state")


def test_turn_on_missing_light_id():
    with pytest.raises(ValueError, match="light_id is required"):
        run(bridge_ip="192.168.1.10", api_key="abc", operation="turn_on")


def test_set_scene_missing_name():
    with pytest.raises(ValueError, match="scene_name is required"):
        run(bridge_ip="192.168.1.10", api_key="abc", operation="set_scene")


# -- Mocked list_lights --

@patch("httpx.Client")
def test_list_lights(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "1": {"name": "Living Room", "type": "Extended color light",
              "state": {"on": True, "bri": 200, "reachable": True}},
    }
    mock_client.get.return_value = mock_resp

    result = run(bridge_ip="192.168.1.10", api_key="abc", operation="list_lights")
    assert result["total"] == 1
    assert result["lights"][0]["name"] == "Living Room"
    assert result["lights"][0]["on"] is True


# -- Mocked turn_on --

@patch("httpx.Client")
def test_turn_on(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_client.put.return_value = mock_resp

    result = run(bridge_ip="192.168.1.10", api_key="abc", operation="turn_on",
                 light_id="1", brightness=150)
    assert result["status"] == "success"
    assert result["light_id"] == "1"
    assert result["settings"]["bri"] == 150
