"""Azure toolkit for managing resource groups and virtual machines."""

from __future__ import annotations

from azure.identity import DefaultAzureCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.resource import ResourceManagementClient


def _get_credential(**kwargs):
    """Obtain an Azure credential using DefaultAzureCredential."""
    return DefaultAzureCredential()


def _list_resource_groups(credential, subscription_id: str, **kwargs) -> dict:
    """List all resource groups in the subscription."""
    rm_client = ResourceManagementClient(credential, subscription_id)
    groups = []
    for rg in rm_client.resource_groups.list():
        groups.append({
            "name": rg.name,
            "location": rg.location,
            "provisioning_state": rg.properties.provisioning_state if rg.properties else None,
            "tags": dict(rg.tags) if rg.tags else {},
        })
    return {
        "status": "ok",
        "subscription_id": subscription_id,
        "resource_groups": groups,
        "count": len(groups),
    }


def _list_vms(credential, subscription_id: str, resource_group: str, **kwargs) -> dict:
    """List virtual machines in a resource group."""
    compute_client = ComputeManagementClient(credential, subscription_id)
    vms = []
    for vm in compute_client.virtual_machines.list(resource_group):
        vms.append({
            "name": vm.name,
            "location": vm.location,
            "vm_size": vm.hardware_profile.vm_size if vm.hardware_profile else None,
            "os_type": vm.storage_profile.os_disk.os_type if vm.storage_profile and vm.storage_profile.os_disk else None,
            "provisioning_state": vm.provisioning_state,
            "vm_id": vm.vm_id,
            "tags": dict(vm.tags) if vm.tags else {},
        })
    return {
        "status": "ok",
        "subscription_id": subscription_id,
        "resource_group": resource_group,
        "vms": vms,
        "count": len(vms),
    }


def _vm_status(credential, subscription_id: str, resource_group: str, **kwargs) -> dict:
    """Get the instance view (power state) of a specific VM."""
    vm_name = kwargs["vm_name"]
    compute_client = ComputeManagementClient(credential, subscription_id)
    vm = compute_client.virtual_machines.get(
        resource_group, vm_name, expand="instanceView"
    )
    statuses = []
    if vm.instance_view and vm.instance_view.statuses:
        for s in vm.instance_view.statuses:
            statuses.append({
                "code": s.code,
                "level": str(s.level),
                "display_status": s.display_status,
                "time": s.time.isoformat() if s.time else None,
            })
    power_state = None
    for s in statuses:
        if s["code"].startswith("PowerState/"):
            power_state = s["code"].split("/", 1)[1]
            break
    return {
        "status": "ok",
        "vm_name": vm_name,
        "resource_group": resource_group,
        "provisioning_state": vm.provisioning_state,
        "power_state": power_state,
        "statuses": statuses,
        "vm_size": vm.hardware_profile.vm_size if vm.hardware_profile else None,
        "location": vm.location,
    }


_OPERATIONS = {
    "list_resource_groups": _list_resource_groups,
    "list_vms": _list_vms,
    "vm_status": _vm_status,
}


def run(
    operation: str,
    subscription_id: str = "",
    resource_group: str = "",
    **kwargs,
) -> dict:
    """Manage Azure resources.

    Parameters
    ----------
    operation : str
        One of: list_resource_groups, list_vms, vm_status.
    subscription_id : str
        Azure subscription ID (falls back to AZURE_SUBSCRIPTION_ID env var).
    resource_group : str
        Resource group name (required for list_vms and vm_status).
    **kwargs :
        vm_name : str – required for vm_status.

    Returns
    -------
    dict with ``status`` and operation-specific data.
    """
    import os

    if not subscription_id:
        subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    if not subscription_id:
        return {"status": "error", "message": "subscription_id is required (pass it or set AZURE_SUBSCRIPTION_ID)"}

    handler = _OPERATIONS.get(operation)
    if handler is None:
        return {
            "status": "error",
            "message": f"Unknown operation: {operation}. Valid operations: {', '.join(_OPERATIONS)}",
        }

    try:
        credential = _get_credential(**kwargs)
        return handler(credential, subscription_id, resource_group, **kwargs)
    except KeyError as exc:
        return {"status": "error", "message": f"Missing required parameter: {exc}"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
