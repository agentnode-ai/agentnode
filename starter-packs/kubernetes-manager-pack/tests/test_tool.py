"""Tests for kubernetes-manager-pack."""

from unittest.mock import MagicMock, patch
from datetime import datetime

from kubernetes_manager_pack.tool import run, _pod_to_dict, _deployment_to_dict, _service_to_dict


# -- Pure helpers --

def test_pod_to_dict():
    pod = MagicMock()
    pod.metadata.name = "web-pod"
    pod.metadata.namespace = "default"
    pod.metadata.creation_timestamp = datetime(2024, 1, 1)
    pod.status.phase = "Running"
    pod.status.pod_ip = "10.0.0.5"
    pod.spec.node_name = "node-1"
    cs = MagicMock()
    cs.name = "nginx"
    cs.ready = True
    cs.restart_count = 0
    cs.image = "nginx:latest"
    pod.status.container_statuses = [cs]

    result = _pod_to_dict(pod)
    assert result["name"] == "web-pod"
    assert result["phase"] == "Running"
    assert len(result["containers"]) == 1


def test_deployment_to_dict():
    dep = MagicMock()
    dep.metadata.name = "api-deploy"
    dep.metadata.namespace = "default"
    dep.metadata.creation_timestamp = datetime(2024, 1, 1)
    dep.spec.replicas = 3
    dep.status.ready_replicas = 3
    dep.status.available_replicas = 3
    dep.status.updated_replicas = 3

    result = _deployment_to_dict(dep)
    assert result["name"] == "api-deploy"
    assert result["replicas"] == 3


# -- Input validation --

@patch("kubernetes_manager_pack.tool._load_config")
def test_unknown_operation(mock_config):
    result = run(operation="destroy_cluster")
    assert result["status"] == "error"
    assert "Unknown operation" in result["message"]


def test_config_load_failure():
    with patch("kubernetes_manager_pack.tool._load_config") as mock_config:
        mock_config.side_effect = RuntimeError("Failed to load Kubernetes config: no config")
        result = run(operation="list_pods")
        assert result["status"] == "error"
        assert "Failed to load" in result["message"]


# -- Mocked list_pods --

@patch("kubernetes_manager_pack.tool.client.AppsV1Api")
@patch("kubernetes_manager_pack.tool.client.CoreV1Api")
@patch("kubernetes_manager_pack.tool._load_config")
def test_list_pods(mock_config, mock_core, mock_apps):
    mock_v1 = MagicMock()
    mock_core.return_value = mock_v1

    pod = MagicMock()
    pod.metadata.name = "test-pod"
    pod.metadata.namespace = "default"
    pod.metadata.creation_timestamp = datetime(2024, 1, 1)
    pod.status.phase = "Running"
    pod.status.pod_ip = "10.0.0.1"
    pod.status.container_statuses = []
    pod.spec.node_name = "node-1"
    mock_v1.list_namespaced_pod.return_value.items = [pod]

    result = run(operation="list_pods")
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["pods"][0]["name"] == "test-pod"


# -- Missing parameter for scale --

@patch("kubernetes_manager_pack.tool.client.AppsV1Api")
@patch("kubernetes_manager_pack.tool.client.CoreV1Api")
@patch("kubernetes_manager_pack.tool._load_config")
def test_scale_missing_deployment(mock_config, mock_core, mock_apps):
    result = run(operation="scale", replicas=3)
    assert result["status"] == "error"
    assert "Missing required parameter" in result["message"]
