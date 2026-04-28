"""Tests for docker-manager-pack."""

from unittest.mock import MagicMock, patch

from docker_manager_pack.tool import run, _container_to_dict, _image_to_dict


# -- Pure helpers --

def test_container_to_dict():
    mock_container = MagicMock()
    mock_container.short_id = "abc123"
    mock_container.name = "web"
    mock_container.status = "running"
    mock_container.image.tags = ["nginx:latest"]
    mock_container.ports = {"80/tcp": [{"HostPort": "8080"}]}

    result = _container_to_dict(mock_container)
    assert result["id"] == "abc123"
    assert result["name"] == "web"
    assert result["status"] == "running"
    assert result["image"] == "nginx:latest"


def test_image_to_dict():
    mock_image = MagicMock()
    mock_image.short_id = "sha256:abc"
    mock_image.tags = ["python:3.12"]
    mock_image.attrs = {"Size": 104857600, "Created": "2024-01-01"}

    result = _image_to_dict(mock_image)
    assert result["id"] == "sha256:abc"
    assert result["size_mb"] == 100.0


# -- Input validation --

def test_unknown_operation():
    with patch("docker_manager_pack.tool.docker.from_env") as mock_from_env:
        mock_from_env.return_value = MagicMock()
        result = run(operation="destroy_all")
        assert result["status"] == "error"
        assert "Unknown operation" in result["message"]


# -- Mocked list_containers --

@patch("docker_manager_pack.tool.docker.from_env")
def test_list_containers(mock_from_env):
    mock_client = MagicMock()
    mock_from_env.return_value = mock_client

    mock_container = MagicMock()
    mock_container.short_id = "c1"
    mock_container.name = "app"
    mock_container.status = "running"
    mock_container.image.tags = ["myapp:v1"]
    mock_container.ports = {}
    mock_client.containers.list.return_value = [mock_container]

    result = run(operation="list_containers")
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["containers"][0]["name"] == "app"


# -- Mocked list_images --

@patch("docker_manager_pack.tool.docker.from_env")
def test_list_images(mock_from_env):
    mock_client = MagicMock()
    mock_from_env.return_value = mock_client

    mock_image = MagicMock()
    mock_image.short_id = "sha256:img1"
    mock_image.tags = ["ubuntu:22.04"]
    mock_image.attrs = {"Size": 78643200, "Created": "2024-01-01"}
    mock_client.images.list.return_value = [mock_image]

    result = run(operation="list_images")
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["images"][0]["tags"] == ["ubuntu:22.04"]


# -- Missing parameter --

@patch("docker_manager_pack.tool.docker.from_env")
def test_start_missing_container_id(mock_from_env):
    mock_from_env.return_value = MagicMock()
    result = run(operation="start")
    assert result["status"] == "error"
    assert "Missing required parameter" in result["message"]


# -- Docker connection error --

@patch("docker_manager_pack.tool.docker.from_env")
def test_docker_connection_error(mock_from_env):
    from docker.errors import DockerException
    mock_from_env.side_effect = DockerException("Cannot connect")

    result = run(operation="list_containers")
    assert result["status"] == "error"
    assert "Cannot connect" in result["message"]
