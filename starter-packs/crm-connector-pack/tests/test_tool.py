"""Tests for crm-connector-pack."""

import pytest
from unittest.mock import MagicMock, patch

from crm_connector_pack.tool import run


# -- Input validation --

def test_unsupported_provider():
    with pytest.raises(ValueError, match="Unsupported provider"):
        run(provider="salesforce", api_key="key", operation="list_contacts")


def test_unknown_operation():
    with pytest.raises(ValueError, match="Unknown operation"):
        run(provider="hubspot", api_key="key", operation="delete_all")


def test_create_contact_missing_email():
    with pytest.raises(ValueError, match="email is required"):
        run(provider="hubspot", api_key="key", operation="create_contact")


def test_get_contact_missing_id():
    with pytest.raises(ValueError, match="contact_id is required"):
        run(provider="hubspot", api_key="key", operation="get_contact")


# -- Mocked list_contacts --

@patch("crm_connector_pack.tool.httpx.Client")
def test_list_contacts(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "results": [{
            "id": "101", "createdAt": "2024-01-01",
            "properties": {"email": "a@b.com", "firstname": "Alice", "lastname": "B",
                           "phone": "555", "company": "Acme"},
        }],
    }
    mock_client.get.return_value = mock_resp

    result = run(provider="hubspot", api_key="key", operation="list_contacts")
    assert result["total"] == 1
    assert result["contacts"][0]["email"] == "a@b.com"


# -- Mocked create_contact --

@patch("crm_connector_pack.tool.httpx.Client")
def test_create_contact(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"id": "201"}
    mock_client.post.return_value = mock_resp

    result = run(provider="hubspot", api_key="key", operation="create_contact",
                 email="new@test.com", firstname="Bob")
    assert result["status"] == "created"
    assert result["id"] == "201"


# -- Mocked list_deals --

@patch("crm_connector_pack.tool.httpx.Client")
def test_list_deals(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "results": [{
            "id": "301", "createdAt": "2024-01-01",
            "properties": {"dealname": "Big Deal", "amount": "5000",
                           "dealstage": "closedwon", "closedate": "2024-06-01",
                           "pipeline": "default"},
        }],
    }
    mock_client.get.return_value = mock_resp

    result = run(provider="hubspot", api_key="key", operation="list_deals")
    assert result["total"] == 1
    assert result["deals"][0]["name"] == "Big Deal"
