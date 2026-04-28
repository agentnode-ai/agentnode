"""Tests for azure-toolkit-pack."""

import sys
from unittest.mock import MagicMock, patch

# Pre-mock azure modules so tool.py can be imported without azure SDK installed
for mod_name in [
    "azure", "azure.identity", "azure.mgmt", "azure.mgmt.compute",
    "azure.mgmt.resource",
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

from azure_toolkit_pack.tool import run


# -- Input validation --

def test_missing_subscription_id(monkeypatch):
    monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
    result = run(operation="list_resource_groups", subscription_id="")
    assert result["status"] == "error"
    assert "subscription_id" in result["message"]


def test_unknown_operation():
    result = run(operation="destroy_everything", subscription_id="sub-123")
    assert result["status"] == "error"
    assert "Unknown operation" in result["message"]


# -- Mocked list_resource_groups --

@patch("azure_toolkit_pack.tool._get_credential")
@patch("azure_toolkit_pack.tool.ResourceManagementClient")
def test_list_resource_groups(mock_rm_cls, mock_cred):
    mock_cred.return_value = MagicMock()

    mock_rg = MagicMock()
    mock_rg.name = "rg-prod"
    mock_rg.location = "eastus"
    mock_rg.properties.provisioning_state = "Succeeded"
    mock_rg.tags = {"env": "production"}

    mock_rm = MagicMock()
    mock_rm.resource_groups.list.return_value = [mock_rg]
    mock_rm_cls.return_value = mock_rm

    result = run(operation="list_resource_groups", subscription_id="sub-123")
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["resource_groups"][0]["name"] == "rg-prod"
    assert result["resource_groups"][0]["location"] == "eastus"


# -- Mocked list_vms --

@patch("azure_toolkit_pack.tool._get_credential")
@patch("azure_toolkit_pack.tool.ComputeManagementClient")
def test_list_vms(mock_compute_cls, mock_cred):
    mock_cred.return_value = MagicMock()

    mock_vm = MagicMock()
    mock_vm.name = "vm-web"
    mock_vm.location = "westus"
    mock_vm.hardware_profile.vm_size = "Standard_D2s_v3"
    mock_vm.storage_profile.os_disk.os_type = "Linux"
    mock_vm.provisioning_state = "Succeeded"
    mock_vm.vm_id = "vm-123"
    mock_vm.tags = {}

    mock_compute = MagicMock()
    mock_compute.virtual_machines.list.return_value = [mock_vm]
    mock_compute_cls.return_value = mock_compute

    result = run(operation="list_vms", subscription_id="sub-123", resource_group="rg-prod")
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["vms"][0]["name"] == "vm-web"


# -- Missing parameter --

@patch("azure_toolkit_pack.tool._get_credential")
@patch("azure_toolkit_pack.tool.ComputeManagementClient")
def test_vm_status_missing_name(mock_compute_cls, mock_cred):
    mock_cred.return_value = MagicMock()
    mock_compute_cls.return_value = MagicMock()

    result = run(operation="vm_status", subscription_id="sub-123", resource_group="rg")
    assert result["status"] == "error"
    assert "Missing required parameter" in result["message"]
