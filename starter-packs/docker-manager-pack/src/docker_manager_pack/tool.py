"""Docker management tool using docker-py."""

from __future__ import annotations

import docker
from docker.errors import DockerException, NotFound, APIError


def _get_client(**kwargs) -> docker.DockerClient:
    """Create a Docker client from environment or explicit base_url."""
    base_url = kwargs.get("base_url")
    if base_url:
        return docker.DockerClient(base_url=base_url)
    return docker.from_env()


def _container_to_dict(container) -> dict:
    """Convert a container object to a serialisable dict."""
    return {
        "id": container.short_id,
        "name": container.name,
        "status": container.status,
        "image": str(container.image.tags[0]) if container.image.tags else str(container.image.id[:12]),
        "ports": container.ports,
    }


def _image_to_dict(image) -> dict:
    """Convert an image object to a serialisable dict."""
    return {
        "id": image.short_id,
        "tags": image.tags,
        "size_mb": round(image.attrs.get("Size", 0) / 1_048_576, 2),
        "created": image.attrs.get("Created", ""),
    }


def run(operation: str, **kwargs) -> dict:
    """Manage Docker containers and images.

    Parameters
    ----------
    operation : str
        One of: list_containers, start, stop, logs, list_images, pull, inspect.
    **kwargs :
        container_id : str – required for start / stop / logs / inspect.
        tail : int – number of log lines (default 100).
        image : str – required for pull.
        base_url : str – optional Docker daemon URL.

    Returns
    -------
    dict with ``status`` and operation-specific data.
    """
    try:
        client = _get_client(**kwargs)
    except DockerException as exc:
        return {"status": "error", "message": f"Cannot connect to Docker: {exc}"}

    try:
        if operation == "list_containers":
            all_flag = kwargs.get("all", True)
            containers = client.containers.list(all=all_flag)
            return {
                "status": "ok",
                "containers": [_container_to_dict(c) for c in containers],
                "count": len(containers),
            }

        elif operation == "start":
            container_id = kwargs["container_id"]
            container = client.containers.get(container_id)
            container.start()
            return {"status": "ok", "message": f"Container {container_id} started", "container": _container_to_dict(container)}

        elif operation == "stop":
            container_id = kwargs["container_id"]
            timeout = kwargs.get("timeout", 10)
            container = client.containers.get(container_id)
            container.stop(timeout=timeout)
            container.reload()
            return {"status": "ok", "message": f"Container {container_id} stopped", "container": _container_to_dict(container)}

        elif operation == "logs":
            container_id = kwargs["container_id"]
            tail = kwargs.get("tail", 100)
            container = client.containers.get(container_id)
            log_output = container.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")
            lines = log_output.strip().splitlines()
            return {
                "status": "ok",
                "container_id": container_id,
                "lines": lines,
                "line_count": len(lines),
            }

        elif operation == "list_images":
            images = client.images.list()
            return {
                "status": "ok",
                "images": [_image_to_dict(i) for i in images],
                "count": len(images),
            }

        elif operation == "pull":
            image = kwargs["image"]
            pulled = client.images.pull(image)
            return {
                "status": "ok",
                "message": f"Pulled {image}",
                "image": _image_to_dict(pulled),
            }

        elif operation == "inspect":
            container_id = kwargs["container_id"]
            container = client.containers.get(container_id)
            attrs = container.attrs
            return {
                "status": "ok",
                "container_id": container_id,
                "name": attrs.get("Name", ""),
                "state": attrs.get("State", {}),
                "config": {
                    "image": attrs.get("Config", {}).get("Image", ""),
                    "env": attrs.get("Config", {}).get("Env", []),
                    "cmd": attrs.get("Config", {}).get("Cmd", []),
                },
                "network": attrs.get("NetworkSettings", {}).get("Networks", {}),
                "mounts": attrs.get("Mounts", []),
            }

        else:
            return {
                "status": "error",
                "message": f"Unknown operation: {operation}. "
                "Valid operations: list_containers, start, stop, logs, list_images, pull, inspect",
            }

    except KeyError as exc:
        return {"status": "error", "message": f"Missing required parameter: {exc}"}
    except NotFound as exc:
        return {"status": "error", "message": f"Not found: {exc}"}
    except APIError as exc:
        return {"status": "error", "message": f"Docker API error: {exc}"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
